from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta
from typing import Any

from otomekairo.capabilities import capability_manifests
from otomekairo.llm import LLMError
from otomekairo.memory_utils import (
    display_local_iso,
    llm_local_time_text,
    local_datetime,
    local_now,
    localize_timestamp_fields,
    now_iso,
    stable_json,
)
from otomekairo.recall import RecallPackSelectionError
from otomekairo.service_capability import CapabilityDispatchError
from otomekairo.service_common import ServiceError, debug_log


RECALL_HINT_RECENT_TURN_LIMIT = 6
VISUAL_OBSERVATION_IMAGE_LIMIT = 1
WORLD_STATE_FOREGROUND_LIMIT = 4
WORLD_STATE_MAX_ACTIVE = 12
WORLD_STATE_HINT_SCORES = {
    "low": 0.35,
    "medium": 0.65,
    "high": 0.85,
}
WORLD_STATE_TTL_SECONDS_BY_TYPE = {
    "screen": {
        "visual_summary_text": {"short": 600, "medium": 900, "long": 1800},
        "window_title": {"short": 300, "medium": 600, "long": 1200},
        "active_app": {"short": 300, "medium": 600, "long": 900},
        "summary_text": {"short": 300, "medium": 600, "long": 900},
    },
    "environment": {
        "summary_text": {"short": 900, "medium": 2400, "long": 7200},
    },
    "location": {
        "summary_text": {"short": 1800, "medium": 3600, "long": 14400},
    },
    "external_service": {
        "status_text": {"short": 1800, "medium": 7200, "long": 21600},
        "external_service_summary": {"short": 1200, "medium": 3600, "long": 10800},
        "summary_text": {"short": 1200, "medium": 3600, "long": 10800},
    },
    "body": {
        "body_state_summary": {"short": 900, "medium": 2400, "long": 7200},
        "summary_text": {"short": 900, "medium": 2400, "long": 7200},
    },
    "device": {
        "device_state_summary": {"short": 1200, "medium": 3600, "long": 10800},
        "summary_text": {"short": 900, "medium": 2400, "long": 7200},
    },
    "schedule": {
        "schedule_summary": {"short": 1800, "medium": 5400, "long": 14400},
        "pending_intent": {"short": 900, "medium": 3600, "long": 10800},
        "summary_text": {"short": 1800, "medium": 5400, "long": 14400},
    },
    "social_context": {
        "summary_text": {"short": 900, "medium": 2400, "long": 7200},
    },
}


class ServiceInputMixin:
    # 入力API
    def handle_conversation(self, token: str | None, payload: dict) -> dict[str, Any]:
        # 認可
        state = self._require_token(token)

        # 検証
        input_text = payload.get("text")
        client_context = payload.get("client_context", {})
        input_images = self._normalize_visual_observation_images(payload.get("images"), allow_missing=True)
        if not isinstance(input_text, str):
            raise ServiceError(400, "invalid_text", "The text field must be a string.")
        if not isinstance(client_context, dict):
            raise ServiceError(400, "invalid_client_context", "The client_context field must be an object.")

        current_client_context = dict(client_context)
        observation_summary: dict[str, Any] | None = None

        # スナップショット
        cycle_id = self._new_cycle_id()
        started_at = self._now_iso()
        recent_turns = self._load_recent_turns(state)
        runtime_summary = self._build_runtime_summary(state)
        debug_log(
            "Conversation",
            (
                f"{self._short_cycle_id(cycle_id)} start input_chars={len(input_text)} "
                f"recent_turns={len(recent_turns)} context_keys={self._debug_context_keys(client_context)}"
            ),
        )

        try:
            # 画像観測要約
            if input_images:
                current_client_context["image_count"] = len(input_images)
                observation_summary = {
                    "source": "conversation_input",
                    "image_count": len(input_images),
                    "image_interpreted": False,
                    "error": None,
                }
                current_client_context, observation_summary = self._interpret_visual_observation(
                    state=state,
                    started_at=started_at,
                    trigger_kind="user_message",
                    client_context=current_client_context,
                    observation_summary=observation_summary,
                    input_text=input_text,
                    images=input_images,
                )

            # パイプライン
            pipeline = self._run_input_pipeline(
                state=state,
                started_at=started_at,
                input_text=input_text,
                recent_turns=recent_turns,
                cycle_id=cycle_id,
                trigger_kind="user_message",
                client_context=current_client_context,
                observation_summary=observation_summary,
            )

            # 成功
            response = self._complete_input_success(
                cycle_id=cycle_id,
                started_at=started_at,
                state=state,
                runtime_summary=runtime_summary,
                input_text=input_text,
                client_context=current_client_context,
                pipeline=pipeline,
                observation_summary=observation_summary,
            )
            debug_log(
                "Conversation",
                f"{self._short_cycle_id(cycle_id)} done result={response['result_kind']}",
            )
            return response
        except RecallPackSelectionError as exc:
            debug_log(
                "Conversation",
                (
                    f"{self._short_cycle_id(cycle_id)} failed stage={exc.failure_stage} "
                    f"error={type(exc).__name__}: {self._clamp(str(exc))}"
                ),
            )
            # 失敗永続化
            finished_at = self._now_iso()
            self._persist_cycle_failure(
                cycle_id=cycle_id,
                started_at=started_at,
                finished_at=finished_at,
                state=state,
                runtime_summary=runtime_summary,
                input_text=input_text,
                client_context=current_client_context,
                failure_reason=str(exc),
                recall_trace=self._build_failure_recall_trace(
                    recall_hint=exc.recall_hint_summary,
                    recall_pack_selection=exc.recall_pack_selection,
                ),
                failure_event_kind="recall_pack_selection_failure",
                failure_event_payload={
                    "failure_stage": exc.failure_stage,
                },
                observation_summary=observation_summary,
            )
            self._emit_input_failure_logs(
                cycle_id=cycle_id,
                trigger_kind="user_message",
                input_text=input_text,
                failure_reason=str(exc),
            )
            return {
                "cycle_id": cycle_id,
                "result_kind": "internal_failure",
                "reply": None,
                "capability_request": None,
            }
        except (LLMError, KeyError, ValueError) as exc:
            debug_log(
                "Conversation",
                f"{self._short_cycle_id(cycle_id)} failed error={type(exc).__name__}: {self._clamp(str(exc))}",
            )
            capability_request_summary, ongoing_action_transition_summary = self._exception_capability_dispatch_trace(
                exc
            )
            # 失敗永続化
            finished_at = self._now_iso()
            self._persist_cycle_failure(
                cycle_id=cycle_id,
                started_at=started_at,
                finished_at=finished_at,
                state=state,
                runtime_summary=runtime_summary,
                input_text=input_text,
                client_context=current_client_context,
                failure_reason=str(exc),
                observation_summary=observation_summary,
                capability_request_summary=capability_request_summary,
                ongoing_action_transition_summary=ongoing_action_transition_summary,
            )
            self._emit_input_failure_logs(
                cycle_id=cycle_id,
                trigger_kind="user_message",
                input_text=input_text,
                failure_reason=str(exc),
            )
            return {
                "cycle_id": cycle_id,
                "result_kind": "internal_failure",
                "reply": None,
                "capability_request": None,
            }

    def _run_input_pipeline(
        self,
        *,
        state: dict[str, Any],
        started_at: str,
        input_text: str,
        recent_turns: list[dict[str, Any]],
        cycle_id: str | None = None,
        trigger_kind: str = "user_message",
        client_context: dict[str, Any] | None = None,
        selected_candidate: dict[str, Any] | None = None,
        pending_intent_selection: dict[str, Any] | None = None,
        observation_summary: dict[str, Any] | None = None,
        capability_request_summary: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        cycle_label = self._debug_cycle_label(cycle_id)
        current_client_context = client_context or {}
        effective_input_text = self._pipeline_effective_input_text(
            input_text=input_text,
            trigger_kind=trigger_kind,
            observation_summary=observation_summary,
        )
        debug_log(
            "Pipeline",
            (
                f"{cycle_label} start memory_set={self._short_identifier(state['selected_memory_set_id'])} "
                f"persona={state['selected_persona_id']} preset={state['selected_model_preset_id']} "
                f"input_chars={len(input_text)} recent_turns={len(recent_turns)}"
            ),
        )
        # モデル選択
        selected_preset = state["model_presets"][state["selected_model_preset_id"]]
        recall_role = selected_preset["roles"]["input_interpretation"]
        decision_role = selected_preset["roles"]["decision_generation"]
        reply_role = selected_preset["roles"]["expression_generation"]
        persona = state["personas"][state["selected_persona_id"]]

        # recall_hint生成
        recall_hint_recent_turns = self._recall_hint_recent_turns(recent_turns)
        debug_log("Pipeline", f"{cycle_label} recall_hint start recent_turns={len(recall_hint_recent_turns)}")
        recall_hint = self.llm.generate_recall_hint(
            role_definition=recall_role,
            input_text=effective_input_text,
            recent_turns=recall_hint_recent_turns,
            current_time=started_at,
        )
        debug_log(
            "Pipeline",
            (
                f"{cycle_label} recall_hint done mode={recall_hint['interaction_mode']} "
                f"focus={recall_hint['primary_recall_focus']} confidence={recall_hint['confidence']}"
            ),
        )

        # recall_pack構築
        debug_log("Pipeline", f"{cycle_label} recall_pack start")
        recall_pack = self.recall.build_recall_pack(
            state=state,
            input_text=effective_input_text,
            recall_hint=recall_hint,
        )
        recall_summary = self._summarize_recall_pack(recall_pack)
        debug_log(
            "Pipeline",
            (
                f"{cycle_label} recall_pack done candidates={recall_pack['candidate_count']} "
                f"selected_memory={len(recall_pack['selected_memory_ids'])} "
                f"selected_episode={len(recall_pack['selected_episode_ids'])} "
                f"sections={recall_summary}"
            ),
        )

        # 内部コンテキスト
        debug_log("Pipeline", f"{cycle_label} context start")
        time_context = self._build_time_context(current_time=started_at)
        affect_context = self._build_affect_context(
            state=state,
            recall_hint=recall_hint,
            current_time=started_at,
        )
        drive_state_summary = self._summarize_drive_states(
            self._list_current_drive_states(
                state=state,
                current_time=started_at,
            )
        )
        world_state_trace, foreground_world_state = self._refresh_world_state_context(
            state=state,
            started_at=started_at,
            input_text=effective_input_text,
            trigger_kind=trigger_kind,
            client_context=current_client_context,
            cycle_id=cycle_id,
            selected_candidate=selected_candidate,
            observation_summary=observation_summary,
            capability_request_summary=capability_request_summary,
        )
        ongoing_action_summary = self._summarize_ongoing_action(
            self._current_ongoing_action(
                state=state,
                current_time=started_at,
            )
        )
        capability_decision_view = self._build_capability_decision_view(
            state=state,
            current_time=started_at,
        )
        initiative_context = self._build_initiative_context(
            trigger_kind=trigger_kind,
            client_context=current_client_context,
            drive_state_summary=drive_state_summary,
            foreground_world_state=foreground_world_state,
            ongoing_action_summary=ongoing_action_summary,
            capability_decision_view=capability_decision_view,
            selected_candidate=selected_candidate,
            pending_intent_selection=pending_intent_selection,
        )
        debug_log(
            "Pipeline",
            (
                f"{cycle_label} context done affect_states={len(affect_context.get('affect_states', []))} "
                f"drives={len(drive_state_summary or [])} world_states={len(foreground_world_state or [])} "
                f"ongoing_action={isinstance(ongoing_action_summary, dict)} "
                f"capabilities={len(capability_decision_view or [])} initiative={isinstance(initiative_context, dict)}"
            ),
        )

        # decision生成
        debug_log("Pipeline", f"{cycle_label} decision start")
        decision = self.llm.generate_decision(
            role_definition=decision_role,
            persona=persona,
            input_text=effective_input_text,
            recent_turns=recent_turns,
            time_context=time_context,
            affect_context=affect_context,
            drive_state_summary=drive_state_summary,
            foreground_world_state=foreground_world_state,
            ongoing_action_summary=ongoing_action_summary,
            capability_decision_view=capability_decision_view,
            initiative_context=initiative_context,
            recall_hint=recall_hint,
            recall_pack=recall_pack,
        )
        debug_log(
            "Pipeline",
            f"{cycle_label} decision done kind={decision['kind']} reason={self._clamp(decision['reason_summary'])}",
        )

        # capability request
        dispatched_capability_request_summary: dict[str, Any] | None = None
        ongoing_action_transition_summary: dict[str, Any] | None = None
        if decision["kind"] == "capability_request":
            dispatch_result = self._dispatch_decision_capability_request(
                state=state,
                current_time=started_at,
                decision=decision,
            )
            dispatched_capability_request_summary = dispatch_result.get("capability_request_summary")
            transition_summary = dispatch_result.get("ongoing_action_transition_summary")
            if isinstance(transition_summary, dict):
                ongoing_action_transition_summary = transition_summary
            debug_log(
                "Pipeline",
                (
                    f"{cycle_label} capability dispatched "
                    f"request={dispatched_capability_request_summary.get('request_id') if isinstance(dispatched_capability_request_summary, dict) else '-'}"
                ),
            )

        # 返信
        reply_payload: dict[str, Any] | None = None
        if decision["kind"] == "reply":
            debug_log("Pipeline", f"{cycle_label} reply start")
            reply_payload = self.llm.generate_reply(
                role_definition=reply_role,
                persona=persona,
                input_text=effective_input_text,
                recent_turns=recent_turns,
                time_context=time_context,
                affect_context=affect_context,
                drive_state_summary=drive_state_summary,
                foreground_world_state=foreground_world_state,
                ongoing_action_summary=ongoing_action_summary,
                capability_decision_view=capability_decision_view,
                initiative_context=initiative_context,
                recall_hint=recall_hint,
                recall_pack=recall_pack,
                decision=decision,
            )
            debug_log("Pipeline", f"{cycle_label} reply done reply_chars={len(reply_payload['reply_text'])}")
        else:
            debug_log("Pipeline", f"{cycle_label} reply skipped decision_kind={decision['kind']}")

        # 結果
        debug_log("Pipeline", f"{cycle_label} done")
        return {
            "recall_hint": recall_hint,
            "recall_pack": recall_pack,
            "time_context": time_context,
            "affect_context": affect_context,
            "drive_state_summary": drive_state_summary,
            "foreground_world_state": foreground_world_state,
            "ongoing_action_summary": ongoing_action_summary,
            "capability_decision_view": capability_decision_view,
            "initiative_context": initiative_context,
            "world_state_trace": world_state_trace,
            "decision": decision,
            "reply_payload": reply_payload,
            "capability_request_summary": dispatched_capability_request_summary,
            "ongoing_action_transition_summary": ongoing_action_transition_summary,
        }

    def _normalize_visual_observation_images(
        self,
        images: Any,
        *,
        allow_missing: bool,
    ) -> list[str]:
        if images is None and allow_missing:
            return []
        if not isinstance(images, list):
            raise ServiceError(400, "invalid_images", "images must be an array.")
        normalized_images: list[str] = []
        for image in images:
            if not isinstance(image, str) or not image.strip():
                raise ServiceError(400, "invalid_images", "images must contain non-empty strings.")
            normalized_images.append(image.strip())
            if len(normalized_images) >= VISUAL_OBSERVATION_IMAGE_LIMIT:
                break
        return normalized_images

    def _interpret_visual_observation(
        self,
        *,
        state: dict[str, Any],
        started_at: str,
        trigger_kind: str,
        client_context: dict[str, Any],
        observation_summary: dict[str, Any],
        input_text: str,
        images: list[str],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if not images:
            return client_context, observation_summary

        # role/source pack
        selected_preset = state["model_presets"][state["selected_model_preset_id"]]
        interpretation_role = selected_preset["roles"]["input_interpretation"]
        source_pack = {
            "trigger_kind": trigger_kind,
            "time_context": llm_local_time_text(started_at).replace("\n", " / "),
            "client_context": self._build_world_state_client_context(client_context),
            "observation_summary": self._build_world_state_capability_result_summary(observation_summary) or {},
            "current_input_summary": self._clamp(input_text.strip(), limit=200) or "",
        }

        # 実行
        try:
            payload = self.llm.generate_visual_observation_summary(
                role_definition=interpretation_role,
                source_pack=source_pack,
                images=images,
            )
        except (LLMError, KeyError, ValueError) as exc:
            observation_summary["image_interpretation_error"] = str(exc)
            raise

        # 反映
        visual_summary_text = str(payload["summary_text"]).strip()
        visual_confidence_hint = str(payload["confidence_hint"]).strip()
        enriched_client_context = {
            **client_context,
            "image_summary_text": visual_summary_text,
        }
        enriched_observation_summary = {
            **observation_summary,
            "image_interpreted": True,
            "visual_summary_text": visual_summary_text,
            "visual_confidence_hint": visual_confidence_hint,
        }
        return enriched_client_context, enriched_observation_summary

    def _pipeline_effective_input_text(
        self,
        *,
        input_text: str,
        trigger_kind: str,
        observation_summary: dict[str, Any] | None,
    ) -> str:
        if trigger_kind != "user_message":
            return input_text
        visual_summary_text = self._visual_observation_summary_text(observation_summary)
        if visual_summary_text is None:
            return input_text
        normalized_input_text = input_text.strip()
        if visual_summary_text in normalized_input_text:
            return input_text
        if not normalized_input_text:
            return f"画像観測では、{visual_summary_text}"
        return f"{normalized_input_text} 画像観測では、{visual_summary_text}"

    def _visual_observation_summary_text(self, observation_summary: dict[str, Any] | None) -> str | None:
        if not isinstance(observation_summary, dict):
            return None
        summary_text = observation_summary.get("visual_summary_text")
        if not isinstance(summary_text, str) or not summary_text.strip():
            return None
        return summary_text.strip()

    def _build_initiative_context(
        self,
        *,
        trigger_kind: str,
        client_context: dict[str, Any],
        drive_state_summary: list[dict[str, Any]] | None,
        foreground_world_state: list[dict[str, Any]] | None,
        ongoing_action_summary: dict[str, Any] | None,
        capability_decision_view: list[dict[str, Any]] | None,
        selected_candidate: dict[str, Any] | None,
        pending_intent_selection: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if trigger_kind not in {"wake", "background_wake", "desktop_watch"}:
            return None
        return {
            "trigger_kind": trigger_kind,
            "opportunity_summary": self._initiative_opportunity_summary(
                trigger_kind=trigger_kind,
                client_context=client_context,
                selected_candidate=selected_candidate,
            ),
            "drive_summaries": self._initiative_drive_summaries(drive_state_summary),
            "pending_intent_summaries": self._initiative_pending_intent_summaries(selected_candidate),
            "world_state_summary": foreground_world_state or [],
            "ongoing_action_summary": ongoing_action_summary,
            "capability_summary": self._initiative_capability_summary(capability_decision_view),
            "intervention_risk_summary": self._initiative_intervention_risk_summary(
                trigger_kind=trigger_kind,
                ongoing_action_summary=ongoing_action_summary,
                capability_decision_view=capability_decision_view,
                selected_candidate=selected_candidate,
                pending_intent_selection=pending_intent_selection,
            ),
        }

    def _initiative_opportunity_summary(
        self,
        *,
        trigger_kind: str,
        client_context: dict[str, Any],
        selected_candidate: dict[str, Any] | None,
    ) -> str:
        if trigger_kind == "background_wake":
            if isinstance(selected_candidate, dict):
                return "background wake が来ており、保留中の候補を再評価する機会がある。"
            return "background wake が来ており、直近入力なしで前進可否を見直す機会がある。"
        if trigger_kind == "wake":
            if isinstance(selected_candidate, dict):
                return "manual wake が呼ばれ、保留中の候補を再評価する機会がある。"
            return "manual wake が呼ばれ、今の前進可否を見直す機会がある。"
        active_app = self._client_context_text(client_context.get("active_app"), limit=48)
        if active_app:
            return f"desktop_watch が前景変化を観測しており、{active_app} を中心に今の判断機会がある。"
        return "desktop_watch が前景変化を観測しており、今の判断機会がある。"

    def _initiative_drive_summaries(
        self,
        drive_state_summary: list[dict[str, Any]] | None,
    ) -> list[dict[str, Any]]:
        summaries: list[dict[str, Any]] = []
        for drive_state in drive_state_summary or []:
            if not isinstance(drive_state, dict):
                continue
            summaries.append(
                {
                    "drive_id": drive_state.get("drive_id"),
                    "summary_text": drive_state.get("summary_text"),
                    "salience": drive_state.get("salience"),
                }
            )
        return summaries

    def _initiative_pending_intent_summaries(self, selected_candidate: dict[str, Any] | None) -> list[dict[str, Any]]:
        if not isinstance(selected_candidate, dict):
            return []
        return [
            {
                "intent_kind": selected_candidate.get("intent_kind"),
                "intent_summary": selected_candidate.get("intent_summary"),
                "reason_summary": selected_candidate.get("reason_summary"),
            }
        ]

    def _initiative_capability_summary(
        self,
        capability_decision_view: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        available_ids: list[str] = []
        unavailable_items: list[dict[str, Any]] = []
        for item in capability_decision_view or []:
            if not isinstance(item, dict):
                continue
            capability_id = item.get("id")
            if not isinstance(capability_id, str) or not capability_id:
                continue
            if item.get("available"):
                available_ids.append(capability_id)
                continue
            unavailable_items.append(
                {
                    "id": capability_id,
                    "reason": item.get("unavailable_reason"),
                }
            )
        return {
            "available_count": len(available_ids),
            "available_ids": available_ids,
            "unavailable_count": len(unavailable_items),
            "unavailable_items": unavailable_items[:3],
        }

    def _initiative_intervention_risk_summary(
        self,
        *,
        trigger_kind: str,
        ongoing_action_summary: dict[str, Any] | None,
        capability_decision_view: list[dict[str, Any]] | None,
        selected_candidate: dict[str, Any] | None,
        pending_intent_selection: dict[str, Any] | None,
    ) -> str | None:
        reasons: list[str] = []
        if trigger_kind == "background_wake":
            reasons.append("直近入力のない定期 wake なので、過剰介入は避けたい。")
        if isinstance(ongoing_action_summary, dict) and ongoing_action_summary.get("status") == "waiting_result":
            reasons.append("ongoing_action が結果待ちで、重複介入は抑えたい。")
        capability_summary = self._initiative_capability_summary(capability_decision_view)
        if capability_summary["available_count"] == 0:
            reasons.append("現時点で使える capability が見当たらない。")
        if not isinstance(selected_candidate, dict):
            pool_count = 0
            if isinstance(pending_intent_selection, dict):
                pool_count = int(pending_intent_selection.get("candidate_pool_count", 0))
            if pool_count == 0:
                reasons.append("前景に出す pending_intent 候補はまだ見当たらない。")
        if not reasons:
            return None
        return " / ".join(reasons)

    def _run_wake_pipeline(
        self,
        *,
        state: dict[str, Any],
        started_at: str,
        trigger_kind: str,
        client_context: dict[str, Any],
        recent_turns: list[dict[str, Any]],
        selected_candidate: dict[str, Any] | None,
        pending_intent_selection: dict[str, Any] | None = None,
        cycle_id: str | None = None,
    ) -> tuple[dict[str, Any], str]:
        cycle_label = self._debug_cycle_label(cycle_id)
        # 入力テキスト
        input_text = self._build_wake_input_text(
            state=state,
            client_context=client_context,
            selected_candidate=selected_candidate,
        )
        debug_log(
            "Wake",
            (
                f"{cycle_label} pipeline start selected_candidate="
                f"{selected_candidate.get('candidate_id') if isinstance(selected_candidate, dict) else '-'}"
            ),
        )

        # 起床ポリシー
        due = self._wake_is_due(state=state, current_time=started_at)
        if due["should_skip"]:
            debug_log("Wake", f"{cycle_label} skipped reason={self._clamp(due['reason_summary'])}")
            return self._noop_pipeline(state=state, started_at=started_at, reason_summary=due["reason_summary"]), input_text

        # クールダウン
        cooldown_reason = self._wake_cooldown_reason(current_time=started_at)
        if cooldown_reason is not None:
            self._set_last_wake_at(started_at)
            debug_log("Wake", f"{cycle_label} skipped cooldown={self._clamp(cooldown_reason)}")
            return self._noop_pipeline(state=state, started_at=started_at, reason_summary=cooldown_reason), input_text

        # 候補
        if selected_candidate is None:
            self._set_last_wake_at(started_at)
            if not self._has_autonomous_initiative_context(state=state, current_time=started_at):
                if (
                    isinstance(pending_intent_selection, dict)
                    and pending_intent_selection.get("selected_candidate_ref") == "none"
                    and isinstance(pending_intent_selection.get("selection_reason"), str)
                    and pending_intent_selection["selection_reason"].strip()
                ):
                    reason_summary = pending_intent_selection["selection_reason"].strip()
                else:
                    reason_summary = "起床機会は来たが、再評価すべき pending_intent 候補も自発評価に使う前景状態もまだ無い。"
                debug_log("Wake", f"{cycle_label} skipped no_candidate reason={self._clamp(reason_summary)}")
                return (
                    self._noop_pipeline(
                        state=state,
                        started_at=started_at,
                        reason_summary=reason_summary,
                    ),
                    input_text,
                )
            debug_log("Wake", f"{cycle_label} autonomous path no_selected_candidate")

        # 返信抑制
        if selected_candidate is not None:
            if self._was_recently_replied(
                dedupe_key=selected_candidate["dedupe_key"],
                current_time=started_at,
            ):
                self._set_last_wake_at(started_at)
                debug_log(
                    "Wake",
                    f"{cycle_label} skipped recently_replied candidate={selected_candidate.get('candidate_id')}",
                )
                return (
                    self._noop_pipeline(
                        state=state,
                        started_at=started_at,
                        reason_summary="同じ pending_intent 候補には最近 reply 済みのため、今回は再介入しない。",
                    ),
                    input_text,
                )

            # トリガー集計
            self._set_last_wake_at(started_at)

        # 起床入力
        pipeline = self._run_input_pipeline(
            state=state,
            started_at=started_at,
            input_text=input_text,
            recent_turns=recent_turns,
            cycle_id=cycle_id,
            trigger_kind=trigger_kind,
            client_context=client_context,
            selected_candidate=selected_candidate,
            pending_intent_selection=pending_intent_selection,
        )
        return pipeline, input_text

    def _has_autonomous_initiative_context(
        self,
        *,
        state: dict[str, Any],
        current_time: str,
    ) -> bool:
        drive_state_summary = self._summarize_drive_states(
            self._list_current_drive_states(
                state=state,
                current_time=current_time,
            )
        )
        if drive_state_summary:
            return True
        foreground_world_state = self._summarize_foreground_world_states(
            self._list_current_world_states(
                state=state,
                current_time=current_time,
                limit=WORLD_STATE_FOREGROUND_LIMIT,
            ),
            current_time=current_time,
        )
        if foreground_world_state:
            return True
        ongoing_action_summary = self._summarize_ongoing_action(
            self._current_ongoing_action(
                state=state,
                current_time=current_time,
            )
        )
        return isinstance(ongoing_action_summary, dict)

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

    def _summarize_recall_pack(self, recall_pack: dict[str, Any]) -> dict[str, int]:
        # 要約
        return {
            "self_model": len(recall_pack["self_model"]),
            "user_model": len(recall_pack["user_model"]),
            "relationship_model": len(recall_pack["relationship_model"]),
            "active_topics": len(recall_pack["active_topics"]),
            "active_commitments": len(recall_pack["active_commitments"]),
            "episodic_evidence": len(recall_pack["episodic_evidence"]),
            "event_evidence": len(recall_pack["event_evidence"]),
            "conflicts": len(recall_pack["conflicts"]),
        }

    def _emit_input_success_logs(
        self,
        *,
        cycle_id: str,
        trigger_kind: str,
        input_text: str,
        pipeline: dict[str, Any],
        result_kind: str,
        reply_payload: dict[str, Any] | None,
        pending_intent_selection: dict[str, Any] | None = None,
    ) -> None:
        # recall一覧
        recall_hint = pipeline["recall_hint"]
        recall_pack = pipeline["recall_pack"]
        decision = pipeline["decision"]
        association_memory_ids = set(recall_pack["association_selected_memory_ids"])
        association_episode_ids = set(recall_pack["association_selected_episode_ids"])
        structured_memory_ids = [
            memory_id for memory_id in recall_pack["selected_memory_ids"] if memory_id not in association_memory_ids
        ]
        structured_episode_ids = [
            episode_id
            for episode_id in recall_pack["selected_episode_ids"]
            if episode_id not in association_episode_ids
        ]

        # ログ群
        logs = [
            self._build_live_log_record(
                level="INFO",
                component="Input",
                message=(
                    f"{self._short_cycle_id(cycle_id)} trigger={trigger_kind} "
                    f"input={self._clamp(input_text)}"
                ),
            ),
            self._build_live_log_record(
                level="INFO",
                component="RecallHint",
                message=(
                    f"{self._short_cycle_id(cycle_id)} mode={recall_hint['interaction_mode']} "
                    f"primary={recall_hint['primary_recall_focus']} "
                    f"secondary={self._format_list_for_log(recall_hint['secondary_recall_focuses'])} "
                    f"risk={self._format_list_for_log(recall_hint['risk_flags'])} "
                    f"time={recall_hint['time_reference']} confidence={recall_hint['confidence']}"
                ),
            ),
            self._build_live_log_record(
                level="INFO",
                component="RecallStructured",
                message=(
                    f"{self._short_cycle_id(cycle_id)} "
                    f"memory_units={self._format_id_list_for_log(structured_memory_ids)} "
                    f"episodes={self._format_id_list_for_log(structured_episode_ids)}"
                ),
            ),
            self._build_live_log_record(
                level="INFO",
                component="RecallAssociation",
                message=(
                    f"{self._short_cycle_id(cycle_id)} "
                    f"memory_units={self._format_id_list_for_log(recall_pack['association_selected_memory_ids'])} "
                    f"episodes={self._format_id_list_for_log(recall_pack['association_selected_episode_ids'])}"
                ),
            ),
            self._build_live_log_record(
                level="INFO",
                component="RecallResult",
                message=(
                    f"{self._short_cycle_id(cycle_id)} candidates={recall_pack['candidate_count']} "
                    f"adopted={self._clamp(self._recall_adopted_reason_summary(recall_pack))}"
                ),
            ),
            self._build_live_log_record(
                level="INFO",
                component="Decision",
                message=(
                    f"{self._short_cycle_id(cycle_id)} kind={decision['kind']} "
                    f"reason={self._clamp(decision['reason_summary'])}"
                ),
            ),
            self._build_live_log_record(
                level="INFO",
                component="Result",
                message=(
                    f"{self._short_cycle_id(cycle_id)} result={result_kind} "
                    f"reply={self._clamp(reply_payload['reply_text']) if reply_payload else '-'}"
                ),
            ),
        ]
        if isinstance(pending_intent_selection, dict) and (
            int(pending_intent_selection.get("candidate_pool_count", 0)) > 0
            or str(pending_intent_selection.get("result_status") or "") == "failed"
        ):
            logs.insert(
                1,
                self._build_live_log_record(
                    level="INFO",
                    component="Input",
                    message=(
                        f"{self._short_cycle_id(cycle_id)} pending_intent_selection "
                        f"pool={pending_intent_selection.get('candidate_pool_count', 0)} "
                        f"eligible={pending_intent_selection.get('eligible_candidate_count', 0)} "
                        f"selected={pending_intent_selection.get('selected_candidate_ref') or '-'} "
                        f"status={pending_intent_selection.get('result_status', 'unknown')} "
                        f"reason={self._clamp(str(pending_intent_selection.get('selection_reason') or '-'))}"
                    ),
                ),
            )
        self._log_stream_registry.append_logs(logs)

    def _emit_input_failure_logs(
        self,
        *,
        cycle_id: str,
        trigger_kind: str,
        input_text: str,
        failure_reason: str,
        pending_intent_selection: dict[str, Any] | None = None,
    ) -> None:
        # ログ群
        logs = [
            self._build_live_log_record(
                level="INFO",
                component="Input",
                message=(
                    f"{self._short_cycle_id(cycle_id)} trigger={trigger_kind} "
                    f"input={self._clamp(input_text)}"
                ),
            ),
            self._build_live_log_record(
                level="ERROR",
                component="Failure",
                message=(
                    f"{self._short_cycle_id(cycle_id)} internal_failure "
                    f"reason={self._clamp(failure_reason)}"
                ),
            ),
        ]
        if isinstance(pending_intent_selection, dict) and (
            int(pending_intent_selection.get("candidate_pool_count", 0)) > 0
            or str(pending_intent_selection.get("result_status") or "") == "failed"
        ):
            logs.insert(
                1,
                self._build_live_log_record(
                    level="INFO",
                    component="Input",
                    message=(
                        f"{self._short_cycle_id(cycle_id)} pending_intent_selection "
                        f"pool={pending_intent_selection.get('candidate_pool_count', 0)} "
                        f"eligible={pending_intent_selection.get('eligible_candidate_count', 0)} "
                        f"selected={pending_intent_selection.get('selected_candidate_ref') or '-'} "
                        f"status={pending_intent_selection.get('result_status', 'unknown')} "
                        f"reason={self._clamp(str(pending_intent_selection.get('selection_reason') or '-'))}"
                    ),
                ),
            )
        self._log_stream_registry.append_logs(logs)

    def _emit_memory_trace_logs(self, *, cycle_id: str, memory_trace: dict[str, Any]) -> None:
        # status判定
        status = str(memory_trace.get("turn_consolidation_status", "unknown"))
        if status == "failed":
            level = "WARNING"
            message = (
                f"{self._short_cycle_id(cycle_id)} status=failed "
                f"reason={self._clamp(str(memory_trace.get('failure_reason') or '-'))}"
            )
        elif status == "skipped":
            level = "INFO"
            message = (
                f"{self._short_cycle_id(cycle_id)} status=skipped "
                f"reason={self._clamp(str(memory_trace.get('skip_reason') or '-'))}"
            )
        else:
            vector_sync = memory_trace.get("vector_index_sync") or {}
            reflective = memory_trace.get("reflective_consolidation") or {}
            drive_update = memory_trace.get("drive_state_update") or {}
            message = (
                f"{self._short_cycle_id(cycle_id)} status={status} "
                f"episode={memory_trace.get('episode_id') or '-'} "
                f"memory_actions={memory_trace.get('memory_action_count', 0)} "
                f"episode_affects={memory_trace.get('episode_affect_count', 0)} "
                f"vector={vector_sync.get('result_status', 'unknown')}"
            )
            message += f" reflection={reflective.get('result_status', 'unknown')}"
            message += f" drive={drive_update.get('result_status', 'unknown')}"
            level = "INFO"

        # 送出
        self._log_stream_registry.append_logs(
            [
                self._build_live_log_record(
                    level=level,
                    component="Memory",
                    message=message,
                )
            ]
        )

    def _build_live_log_record(self, *, level: str, component: str, message: str) -> dict[str, Any]:
        # 結果
        return {
            "ts": display_local_iso(self._now_iso()),
            "level": level,
            "logger": component,
            "msg": message,
        }

    def _short_cycle_id(self, cycle_id: str) -> str:
        # 空
        if ":" not in cycle_id:
            return cycle_id[:12]

        # 結果
        return cycle_id.split(":", 1)[1][:12]

    def _debug_cycle_label(self, cycle_id: str | None) -> str:
        # 未採番経路
        if not isinstance(cycle_id, str) or not cycle_id:
            return "-"
        return self._short_cycle_id(cycle_id)

    def _debug_context_keys(self, context: dict[str, Any]) -> str:
        # 値は出さずキーだけに留める。
        keys = sorted(str(key) for key in context.keys())[:8]
        return ",".join(keys) if keys else "-"

    def _format_list_for_log(self, values: list[Any]) -> str:
        # 空
        if not values:
            return "-"

        # 結果
        return ",".join(str(value) for value in values[:3])

    def _format_id_list_for_log(self, values: list[str]) -> str:
        # 空
        if not values:
            return "-"

        # 結果
        return ",".join(self._short_identifier(value) for value in values[:3])

    def _short_identifier(self, value: str) -> str:
        # 空
        if ":" not in value:
            return value[:18]

        # 結果
        prefix, suffix = value.split(":", 1)
        return f"{prefix}:{suffix[:8]}"

    def _external_result_kind(self, internal_result_kind: str) -> str:
        # マッピング
        if internal_result_kind == "pending_intent":
            return "noop"
        return internal_result_kind

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
            "interaction_mode": "autonomous",
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
            "event_evidence_generation": self._empty_event_evidence_generation_trace(),
            "recall_pack_selection": self._empty_recall_pack_selection_trace(),
            "conflicts": [],
            "selected_memory_ids": [],
            "selected_episode_ids": [],
            "association_selected_memory_ids": [],
            "association_selected_episode_ids": [],
            "selected_event_ids": [],
            "candidate_count": 0,
        }

    def _empty_event_evidence_generation_trace(self) -> dict[str, Any]:
        return {
            "requested_event_count": 0,
            "loaded_event_count": 0,
            "succeeded_event_count": 0,
            "failed_items": [],
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

    def _refresh_world_state_context(
        self,
        *,
        state: dict[str, Any],
        started_at: str,
        input_text: str,
        trigger_kind: str,
        client_context: dict[str, Any],
        cycle_id: str | None,
        selected_candidate: dict[str, Any] | None,
        observation_summary: dict[str, Any] | None,
        capability_request_summary: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        existing_foreground_world_state = (
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
        source_kind = self._world_state_source_kind(trigger_kind)
        source_ref = self._world_state_source_ref(
            cycle_id=cycle_id,
            trigger_kind=trigger_kind,
            started_at=started_at,
            capability_request_summary=capability_request_summary,
        )
        source_pack_contexts: dict[str, Any] = {}
        source_pack_state_type_hooks: dict[str, Any] = {}
        try:
            source_pack = self._build_world_state_source_pack(
                started_at=started_at,
                input_text=input_text,
                trigger_kind=trigger_kind,
                client_context=client_context,
                source_kind=source_kind,
                source_ref=source_ref,
                selected_candidate=selected_candidate,
                observation_summary=observation_summary,
                existing_foreground_world_state=existing_foreground_world_state,
            )
            source_pack_contexts = self._summarize_world_state_source_pack_contexts(source_pack)
            source_pack_state_type_hooks = self._summarize_world_state_state_type_hooks(source_pack)
            role_definition = state["model_presets"][state["selected_model_preset_id"]]["roles"]["input_interpretation"]
            payload = self.llm.generate_world_state(
                role_definition=role_definition,
                source_pack=source_pack,
            )
            world_states = self._normalize_world_state_candidates(
                memory_set_id=state["selected_memory_set_id"],
                observed_at=started_at,
                source_kind=source_kind,
                source_ref=source_ref,
                payload=payload,
                source_pack=source_pack,
            )
            normalized_candidate_policies = self._summarize_world_state_candidate_policies(world_states)
            refresh_summary = self.store.refresh_world_states(
                memory_set_id=state["selected_memory_set_id"],
                current_time=started_at,
                world_states=world_states,
                max_active=WORLD_STATE_MAX_ACTIVE,
            )
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
            return (
                {
                    "result_status": "succeeded",
                    "candidate_state_count": len(payload.get("state_candidates", [])),
                    "input_world_state_count": len(foreground_world_state),
                    "previous_foreground_world_state": existing_foreground_world_state,
                    "foreground_world_state": foreground_world_state,
                    "updated_state_count": int(refresh_summary.get("updated_count", 0)),
                    "replaced_state_count": int(refresh_summary.get("replaced_count", 0)),
                    "expired_state_count": int(refresh_summary.get("expired_count", 0)),
                    "dropped_state_count": int(refresh_summary.get("dropped_count", 0)),
                    "source_kind": source_kind,
                    "source_ref": source_ref,
                    "source_pack_contexts": source_pack_contexts,
                    "source_pack_state_type_hooks": source_pack_state_type_hooks,
                    "normalized_candidate_policies": normalized_candidate_policies,
                    "failure_reason": None,
                },
                foreground_world_state,
            )
        except (LLMError, KeyError, TypeError, ValueError) as exc:
            return (
                {
                    "result_status": "failed",
                    "candidate_state_count": 0,
                    "input_world_state_count": len(existing_foreground_world_state),
                    "previous_foreground_world_state": existing_foreground_world_state,
                    "foreground_world_state": existing_foreground_world_state,
                    "updated_state_count": 0,
                    "replaced_state_count": 0,
                    "expired_state_count": 0,
                    "dropped_state_count": 0,
                    "source_kind": source_kind,
                    "source_ref": source_ref,
                    "source_pack_contexts": source_pack_contexts,
                    "source_pack_state_type_hooks": source_pack_state_type_hooks,
                    "normalized_candidate_policies": [],
                    "failure_reason": str(exc),
                },
                existing_foreground_world_state,
            )

    def _build_world_state_source_pack(
        self,
        *,
        started_at: str,
        input_text: str,
        trigger_kind: str,
        client_context: dict[str, Any],
        source_kind: str,
        source_ref: str,
        selected_candidate: dict[str, Any] | None,
        observation_summary: dict[str, Any] | None,
        existing_foreground_world_state: list[dict[str, Any]],
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "trigger_kind": trigger_kind,
            "current_input_summary": self._clamp(input_text.strip(), limit=200) or "",
            "source_kind": source_kind,
            "source_ref": source_ref,
            "time_context": llm_local_time_text(started_at).replace("\n", " / "),
            "client_context": self._build_world_state_client_context(client_context),
            "existing_foreground_world_state": existing_foreground_world_state,
        }
        screen_context = self._build_world_state_screen_context(
            client_context=client_context,
            observation_summary=observation_summary,
        )
        if screen_context is not None:
            payload["screen_context"] = screen_context
        for key, value in (
            (
                "external_service_context",
                self._build_world_state_external_service_context(
                    client_context=client_context,
                    observation_summary=observation_summary,
                ),
            ),
            (
                "body_context",
                self._build_world_state_body_context(
                    client_context=client_context,
                    observation_summary=observation_summary,
                ),
            ),
            (
                "device_context",
                self._build_world_state_device_context(
                    client_context=client_context,
                    observation_summary=observation_summary,
                ),
            ),
            (
                "schedule_context",
                self._build_world_state_schedule_context(
                    client_context=client_context,
                    observation_summary=observation_summary,
                    selected_candidate=selected_candidate,
                ),
            ),
        ):
            if value is not None:
                payload[key] = value
        capability_result_summary = self._build_world_state_capability_result_summary(observation_summary)
        if capability_result_summary is not None:
            payload["capability_result_summary"] = capability_result_summary
        return payload

    def _build_world_state_screen_context(
        self,
        *,
        client_context: dict[str, Any],
        observation_summary: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        payload: dict[str, Any] = {}
        visual_summary_text = None
        capability_id_text = None
        if isinstance(observation_summary, dict):
            visual_summary_text = self._client_context_text(observation_summary.get("visual_summary_text"), limit=160)
            if visual_summary_text is not None:
                payload["summary_text"] = visual_summary_text
                payload["visual_summary_text"] = visual_summary_text
            image_interpreted = observation_summary.get("image_interpreted")
            if isinstance(image_interpreted, bool):
                payload["image_interpreted"] = image_interpreted
            visual_confidence_hint = observation_summary.get("visual_confidence_hint")
            if isinstance(visual_confidence_hint, str) and visual_confidence_hint.strip():
                payload["visual_confidence_hint"] = visual_confidence_hint.strip()
            image_count = observation_summary.get("image_count")
            if isinstance(image_count, int) and image_count >= 0:
                payload["image_count"] = image_count
            capability_id = observation_summary.get("capability_id")
            if isinstance(capability_id, str) and capability_id.strip():
                capability_id_text = capability_id.strip()
        for key, limit in (("active_app", 80), ("window_title", 120), ("locale", 32)):
            value = self._client_context_text(client_context.get(key), limit=limit)
            if value is not None:
                payload[key] = value
        if "summary_text" not in payload:
            window_title = payload.get("window_title")
            active_app = payload.get("active_app")
            if isinstance(window_title, str):
                payload["summary_text"] = f"画面では {window_title} が前景にある。"
            elif isinstance(active_app, str):
                payload["summary_text"] = f"画面では {active_app} が前景にある。"
        has_screen_signal = any(
            key in payload
            for key in ("summary_text", "visual_summary_text", "active_app", "window_title", "image_count", "image_interpreted")
        )
        if not has_screen_signal:
            return None
        if capability_id_text is not None:
            payload["capability_id"] = capability_id_text
        return payload

    def _build_world_state_external_service_context(
        self,
        *,
        client_context: dict[str, Any],
        observation_summary: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        payload: dict[str, Any] = {}
        summary_text = self._client_context_text(client_context.get("external_service_summary"), limit=160)
        capability_id_text = None
        if isinstance(observation_summary, dict):
            status_text = self._client_context_text(observation_summary.get("status_text"), limit=160)
            if summary_text is None:
                summary_text = status_text
            if status_text is not None:
                payload["status_text"] = status_text
            service = self._client_context_text(observation_summary.get("service"), limit=80)
            if service is not None:
                payload["service"] = service
            capability_id = observation_summary.get("capability_id")
            if isinstance(capability_id, str) and capability_id.strip():
                capability_id_text = capability_id.strip()
        has_external_signal = summary_text is not None or "status_text" in payload or "service" in payload
        if not has_external_signal:
            return None
        if summary_text is not None:
            payload["summary_text"] = summary_text
        if capability_id_text is not None:
            payload["capability_id"] = capability_id_text
        return payload

    def _build_world_state_body_context(
        self,
        *,
        client_context: dict[str, Any],
        observation_summary: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        return self._build_world_state_capability_state_context(
            client_context=client_context,
            observation_summary=observation_summary,
            client_summary_key="body_state_summary",
            observation_summary_key="body_state_summary",
            explicit_field_name="body_state_summary",
        )

    def _build_world_state_device_context(
        self,
        *,
        client_context: dict[str, Any],
        observation_summary: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        return self._build_world_state_capability_state_context(
            client_context=client_context,
            observation_summary=observation_summary,
            client_summary_key="device_state_summary",
            observation_summary_key="device_state_summary",
            explicit_field_name="device_state_summary",
        )

    def _build_world_state_capability_state_context(
        self,
        *,
        client_context: dict[str, Any],
        observation_summary: dict[str, Any] | None,
        client_summary_key: str,
        observation_summary_key: str,
        explicit_field_name: str,
    ) -> dict[str, Any] | None:
        payload: dict[str, Any] = {}
        summary_text = self._client_context_text(client_context.get(client_summary_key), limit=160)
        capability_id_text = None
        if isinstance(observation_summary, dict):
            observation_text = self._client_context_text(observation_summary.get(observation_summary_key), limit=160)
            if summary_text is None:
                summary_text = observation_text
            capability_id = observation_summary.get("capability_id")
            if isinstance(capability_id, str) and capability_id.strip():
                capability_id_text = capability_id.strip()
        if summary_text is None:
            return None
        payload["summary_text"] = summary_text
        payload[explicit_field_name] = summary_text
        if capability_id_text is not None:
            payload["capability_id"] = capability_id_text
        return payload

    def _build_world_state_client_context(self, client_context: dict[str, Any]) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for key, limit in (
            ("source", 48),
            ("active_app", 80),
            ("window_title", 120),
            ("locale", 32),
            ("image_summary_text", 160),
        ):
            value = client_context.get(key)
            if isinstance(value, str) and value.strip():
                payload[key] = self._clamp(value.strip(), limit=limit)
        image_count = client_context.get("image_count")
        if isinstance(image_count, int) and image_count >= 0:
            payload["image_count"] = image_count
        return payload

    def _build_world_state_summary_context(
        self,
        *,
        client_context: dict[str, Any],
        summary_key: str,
        limit: int,
    ) -> dict[str, Any] | None:
        summary_text = self._client_context_text(client_context.get(summary_key), limit=limit)
        if summary_text is None:
            return None
        return {
            "summary_text": summary_text,
        }

    def _build_world_state_schedule_context(
        self,
        *,
        client_context: dict[str, Any],
        observation_summary: dict[str, Any] | None,
        selected_candidate: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        payload: dict[str, Any] = {}
        summary_text = self._client_context_text(client_context.get("schedule_summary"), limit=160)
        capability_id_text = None
        if isinstance(observation_summary, dict):
            observation_text = self._client_context_text(observation_summary.get("schedule_summary"), limit=160)
            if summary_text is None:
                summary_text = observation_text
            capability_id = observation_summary.get("capability_id")
            if isinstance(capability_id, str) and capability_id.strip():
                capability_id_text = capability_id.strip()
        if summary_text is not None:
            payload["summary_text"] = summary_text
            payload["schedule_summary"] = summary_text
        pending_intent = self._build_world_state_pending_intent_context(selected_candidate)
        if pending_intent is not None:
            payload["pending_intent"] = pending_intent
        if capability_id_text is not None and summary_text is not None:
            payload["capability_id"] = capability_id_text
        if not payload:
            return None
        return payload

    def _build_world_state_pending_intent_context(
        self,
        selected_candidate: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if not isinstance(selected_candidate, dict):
            return None
        payload: dict[str, Any] = {}
        for key, limit in (
            ("intent_kind", 48),
            ("intent_summary", 120),
            ("reason_summary", 160),
        ):
            value = self._client_context_text(selected_candidate.get(key), limit=limit)
            if value is not None:
                payload[key] = value
        for key in ("not_before", "expires_at"):
            value = selected_candidate.get(key)
            if isinstance(value, str) and value.strip():
                payload[key] = value.strip()
        slot_key = self._world_state_schedule_slot_key(selected_candidate)
        if slot_key is not None:
            payload["slot_key"] = slot_key
        if not payload:
            return None
        return payload

    def _build_world_state_capability_result_summary(
        self,
        observation_summary: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if not isinstance(observation_summary, dict):
            return None
        payload: dict[str, Any] = {}
        for key in (
            "capability_id",
            "image_count",
            "image_interpreted",
            "visual_summary_text",
            "visual_confidence_hint",
            "service",
            "status_text",
            "body_state_summary",
            "device_state_summary",
            "schedule_summary",
            "error",
        ):
            value = observation_summary.get(key)
            if value is None:
                continue
            payload[key] = value
        if not payload:
            return None
        return payload

    def _summarize_world_state_source_pack_contexts(self, source_pack: dict[str, Any]) -> dict[str, Any]:
        summary: dict[str, Any] = {}
        for key in (
            "client_context",
            "screen_context",
            "external_service_context",
            "body_context",
            "device_context",
            "schedule_context",
            "capability_result_summary",
        ):
            value = source_pack.get(key)
            if isinstance(value, dict) and value:
                summary[key] = value
        return summary

    def _summarize_world_state_state_type_hooks(self, source_pack: dict[str, Any]) -> dict[str, Any]:
        hooks: dict[str, Any] = {}
        for state_type, context_key in (
            ("screen", "screen_context"),
            ("external_service", "external_service_context"),
            ("body", "body_context"),
            ("device", "device_context"),
            ("schedule", "schedule_context"),
        ):
            context = source_pack.get(context_key)
            if not isinstance(context, dict) or not context:
                continue
            hook = self._build_world_state_state_type_hook(state_type=state_type, context=context)
            if hook is not None:
                hooks[state_type] = hook
        return hooks

    def _build_world_state_state_type_hook(
        self,
        *,
        state_type: str,
        context: dict[str, Any],
    ) -> dict[str, Any] | None:
        summary_text = self._client_context_text(context.get("summary_text"), limit=160)
        if summary_text is None:
            return None
        payload: dict[str, Any] = {
            "summary_text": summary_text,
            "summary_source": self._world_state_hook_summary_source(state_type=state_type, context=context),
            "signal_fields": self._world_state_hook_signal_fields(state_type=state_type, context=context),
        }
        capability_id = self._client_context_text(context.get("capability_id"), limit=80)
        if capability_id is not None:
            payload["capability_id"] = capability_id
        if state_type == "screen":
            for key, limit in (("active_app", 80), ("window_title", 120)):
                value = self._client_context_text(context.get(key), limit=limit)
                if value is not None:
                    payload[key] = value
        elif state_type == "external_service":
            service = self._client_context_text(context.get("service"), limit=80)
            if service is not None:
                payload["service"] = service
        elif state_type == "schedule":
            pending_intent = context.get("pending_intent")
            if isinstance(pending_intent, dict):
                pending_summary = self._client_context_text(pending_intent.get("intent_summary"), limit=120)
                if pending_summary is not None:
                    payload["pending_intent_summary"] = pending_summary
                slot_key = self._client_context_text(pending_intent.get("slot_key"), limit=160)
                if slot_key is not None:
                    payload["pending_intent_slot_key"] = slot_key
        return payload

    def _world_state_hook_summary_source(self, *, state_type: str, context: dict[str, Any]) -> str:
        if state_type == "screen":
            if isinstance(context.get("visual_summary_text"), str) and context["visual_summary_text"].strip():
                return "visual_summary_text"
            if isinstance(context.get("window_title"), str) and context["window_title"].strip():
                return "window_title"
            if isinstance(context.get("active_app"), str) and context["active_app"].strip():
                return "active_app"
            return "summary_text"
        if state_type == "external_service":
            if isinstance(context.get("status_text"), str) and context["status_text"].strip():
                return "status_text"
            return "external_service_summary"
        if state_type == "body":
            return "body_state_summary"
        if state_type == "device":
            return "device_state_summary"
        if state_type == "schedule":
            if isinstance(context.get("schedule_summary"), str) and context["schedule_summary"].strip():
                return "schedule_summary"
            if isinstance(context.get("pending_intent"), dict):
                return "pending_intent"
        return "summary_text"

    def _world_state_hook_signal_fields(self, *, state_type: str, context: dict[str, Any]) -> list[str]:
        keys_by_state_type = {
            "screen": (
                "visual_summary_text",
                "image_interpreted",
                "visual_confidence_hint",
                "image_count",
                "active_app",
                "window_title",
                "locale",
            ),
            "external_service": (
                "service",
                "status_text",
            ),
            "body": (
                "body_state_summary",
            ),
            "device": (
                "device_state_summary",
            ),
            "schedule": (
                "schedule_summary",
                "pending_intent",
            ),
        }
        signal_fields: list[str] = []
        for key in keys_by_state_type.get(state_type, ()):
            value = context.get(key)
            if isinstance(value, str):
                if value.strip():
                    signal_fields.append(key)
            elif isinstance(value, dict):
                if value:
                    signal_fields.append(key)
            elif isinstance(value, (int, float, bool)):
                signal_fields.append(key)
        return signal_fields

    def _normalize_world_state_candidates(
        self,
        *,
        memory_set_id: str,
        observed_at: str,
        source_kind: str,
        source_ref: str,
        payload: dict[str, Any],
        source_pack: dict[str, Any],
    ) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        seen_identity: set[tuple[str, str, str]] = set()
        for candidate in payload.get("state_candidates", []):
            if not isinstance(candidate, dict):
                continue
            state_type = str(candidate["state_type"]).strip()
            scope_type, scope_key = self._parse_world_state_scope(str(candidate["scope"]).strip())
            identity = (state_type, scope_type, scope_key)
            if identity in seen_identity:
                continue
            seen_identity.add(identity)
            source_context = self._world_state_source_context(
                state_type=state_type,
                source_pack=source_pack,
            )
            ttl_hint = str(candidate["ttl_hint"]).strip()
            ttl_policy = self._world_state_ttl_policy(
                current_time=observed_at,
                state_type=state_type,
                ttl_hint=ttl_hint,
                context=source_context,
            )
            integration_policy = self._world_state_integration_policy(
                state_type=state_type,
                scope_type=scope_type,
                scope_key=scope_key,
                context=source_context,
            )
            normalized.append(
                {
                    "world_state_id": f"world_state:{uuid.uuid4().hex}",
                    "memory_set_id": memory_set_id,
                    "state_type": state_type,
                    "scope_type": scope_type,
                    "scope_key": scope_key,
                    "summary_text": str(candidate["summary_text"]).strip(),
                    "source_kind": source_kind,
                    "source_ref": source_ref,
                    "confidence": self._world_state_score_from_hint(candidate["confidence_hint"]),
                    "salience": self._world_state_score_from_hint(candidate["salience_hint"]),
                    "observed_at": observed_at,
                    "expires_at": ttl_policy["expires_at"],
                    "updated_at": observed_at,
                    "summary_source": ttl_policy["summary_source"],
                    "ttl_hint": ttl_hint,
                    "ttl_seconds": ttl_policy["ttl_seconds"],
                    "integration_mode": integration_policy["mode"],
                    "integration_key": integration_policy["key"],
                }
            )
            if ttl_policy.get("capped_by") is not None:
                normalized[-1]["ttl_capped_by"] = ttl_policy["capped_by"]
        normalized.sort(key=lambda record: (record["salience"], record["updated_at"]), reverse=True)
        return normalized

    def _world_state_source_context(
        self,
        *,
        state_type: str,
        source_pack: dict[str, Any],
    ) -> dict[str, Any] | None:
        context_key = {
            "screen": "screen_context",
            "external_service": "external_service_context",
            "body": "body_context",
            "device": "device_context",
            "schedule": "schedule_context",
        }.get(state_type)
        if context_key is None:
            return None
        context = source_pack.get(context_key)
        if not isinstance(context, dict) or not context:
            return None
        return context

    def _world_state_ttl_policy(
        self,
        *,
        current_time: str,
        state_type: str,
        ttl_hint: str,
        context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        summary_source = self._world_state_candidate_summary_source(
            state_type=state_type,
            context=context,
        )
        ttl_profiles = WORLD_STATE_TTL_SECONDS_BY_TYPE.get(state_type)
        if ttl_profiles is None:
            raise ValueError("world_state ttl is invalid.")
        ttl_table = ttl_profiles.get(summary_source) or ttl_profiles.get("summary_text")
        if ttl_table is None or ttl_hint not in ttl_table:
            raise ValueError("world_state ttl is invalid.")
        ttl_seconds = ttl_table[ttl_hint]
        ttl_capped_by = self._world_state_ttl_cap_source(
            current_time=current_time,
            state_type=state_type,
            context=context,
        )
        if ttl_capped_by is not None:
            ttl_seconds = min(
                ttl_seconds,
                self._world_state_capped_ttl_seconds(
                    current_time=current_time,
                    state_type=state_type,
                    context=context,
                ),
            )
        return {
            "summary_source": summary_source,
            "ttl_seconds": ttl_seconds,
            "expires_at": (self._parse_iso(current_time) + timedelta(seconds=ttl_seconds)).isoformat(),
            "capped_by": ttl_capped_by,
        }

    def _world_state_candidate_summary_source(
        self,
        *,
        state_type: str,
        context: dict[str, Any] | None,
    ) -> str:
        if not isinstance(context, dict) or not context:
            return "summary_text"
        if state_type in {"screen", "external_service", "body", "device", "schedule"}:
            return self._world_state_hook_summary_source(state_type=state_type, context=context)
        return "summary_text"

    def _world_state_ttl_cap_source(
        self,
        *,
        current_time: str,
        state_type: str,
        context: dict[str, Any] | None,
    ) -> str | None:
        if state_type != "schedule" or not isinstance(context, dict):
            return None
        pending_intent = context.get("pending_intent")
        if not isinstance(pending_intent, dict):
            return None
        expires_at = pending_intent.get("expires_at")
        if not isinstance(expires_at, str) or not expires_at.strip():
            return None
        remaining_seconds = int((self._parse_iso(expires_at.strip()) - self._parse_iso(current_time)).total_seconds())
        if remaining_seconds <= 0:
            return "pending_intent.expires_at"
        return "pending_intent.expires_at"

    def _world_state_capped_ttl_seconds(
        self,
        *,
        current_time: str,
        state_type: str,
        context: dict[str, Any] | None,
    ) -> int:
        if state_type != "schedule" or not isinstance(context, dict):
            raise ValueError("world_state ttl cap is invalid.")
        pending_intent = context.get("pending_intent")
        if not isinstance(pending_intent, dict):
            raise ValueError("world_state ttl cap is invalid.")
        expires_at = pending_intent.get("expires_at")
        if not isinstance(expires_at, str) or not expires_at.strip():
            raise ValueError("world_state ttl cap is invalid.")
        remaining_seconds = int((self._parse_iso(expires_at.strip()) - self._parse_iso(current_time)).total_seconds())
        return max(1, remaining_seconds)

    def _world_state_integration_policy(
        self,
        *,
        state_type: str,
        scope_type: str,
        scope_key: str,
        context: dict[str, Any] | None,
    ) -> dict[str, str]:
        if state_type == "screen":
            return {"mode": "foreground_screen", "key": "screen:foreground"}
        if state_type == "external_service":
            service_key = self._world_state_service_key(context)
            if service_key is not None:
                return {"mode": "external_service_service", "key": f"external_service:{service_key}"}
            return {"mode": "scope", "key": f"{state_type}:{scope_type}:{scope_key}"}
        if state_type == "body":
            return {"mode": "body_foreground", "key": "body:self"}
        if state_type == "device":
            return {"mode": "device_foreground", "key": "device:foreground"}
        if state_type == "schedule":
            schedule_slot_key = self._world_state_schedule_context_slot_key(context)
            if schedule_slot_key is not None:
                return {"mode": "schedule_slot", "key": f"schedule:{schedule_slot_key}"}
            return {"mode": "schedule_foreground", "key": "schedule:self"}
        return {"mode": "scope", "key": f"{state_type}:{scope_type}:{scope_key}"}

    def _world_state_service_key(self, context: dict[str, Any] | None) -> str | None:
        if not isinstance(context, dict):
            return None
        service = self._client_context_text(context.get("service"), limit=80)
        if service is None:
            return None
        normalized = "".join(character if character.isalnum() else "_" for character in service.lower()).strip("_")
        return normalized or None

    def _world_state_schedule_context_slot_key(self, context: dict[str, Any] | None) -> str | None:
        if not isinstance(context, dict):
            return None
        pending_intent = context.get("pending_intent")
        if not isinstance(pending_intent, dict):
            return None
        slot_key = self._client_context_text(pending_intent.get("slot_key"), limit=160)
        if slot_key is None:
            return None
        return slot_key

    def _world_state_schedule_slot_key(self, selected_candidate: dict[str, Any]) -> str | None:
        dedupe_key = self._client_context_text(selected_candidate.get("dedupe_key"), limit=160)
        if dedupe_key is not None:
            return dedupe_key
        not_before = selected_candidate.get("not_before")
        if isinstance(not_before, str) and not_before.strip():
            return f"at:{not_before.strip()}"
        intent_summary = self._client_context_text(selected_candidate.get("intent_summary"), limit=120)
        if intent_summary is not None:
            return f"summary:{intent_summary}"
        return None

    def _summarize_world_state_candidate_policies(
        self,
        world_states: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        summaries: list[dict[str, Any]] = []
        for world_state in world_states:
            if not isinstance(world_state, dict):
                continue
            scope_type = world_state.get("scope_type")
            scope_key = world_state.get("scope_key")
            if not isinstance(scope_type, str) or not isinstance(scope_key, str):
                continue
            summary = {
                "state_type": world_state.get("state_type"),
                "scope": self._world_state_scope_ref(scope_type=scope_type, scope_key=scope_key),
                "summary_source": world_state.get("summary_source"),
                "ttl_hint": world_state.get("ttl_hint"),
                "effective_ttl_seconds": world_state.get("ttl_seconds"),
                "integration_mode": world_state.get("integration_mode"),
                "integration_key": world_state.get("integration_key"),
            }
            ttl_capped_by = world_state.get("ttl_capped_by")
            if isinstance(ttl_capped_by, str) and ttl_capped_by.strip():
                summary["ttl_capped_by"] = ttl_capped_by.strip()
            summaries.append(summary)
        return summaries

    def _world_state_source_kind(self, trigger_kind: str) -> str:
        if trigger_kind == "user_message":
            return "user_input"
        if trigger_kind == "desktop_watch":
            return "system_observation"
        if trigger_kind == "capability_result":
            return "capability_result"
        return "client_context"

    def _world_state_source_ref(
        self,
        *,
        cycle_id: str | None,
        trigger_kind: str,
        started_at: str,
        capability_request_summary: dict[str, Any] | None,
    ) -> str:
        if isinstance(capability_request_summary, dict):
            request_id = capability_request_summary.get("request_id")
            if isinstance(request_id, str) and request_id.strip():
                return request_id.strip()
        if isinstance(cycle_id, str) and cycle_id:
            return cycle_id
        return f"{trigger_kind}:{started_at}"

    def _parse_world_state_scope(self, value: str) -> tuple[str, str]:
        if value in {"self", "user", "world"}:
            return value, value
        scope_type, separator, scope_key = value.partition(":")
        normalized_scope_key = scope_key.strip()
        if not separator or not normalized_scope_key:
            raise ValueError("world_state scope is invalid.")
        if scope_type == "entity":
            if not any(
                normalized_scope_key.startswith(prefix) and normalized_scope_key != prefix
                for prefix in ("person:", "place:", "tool:")
            ):
                raise ValueError("world_state entity scope is invalid.")
            return "entity", normalized_scope_key
        if scope_type == "topic":
            return "topic", value
        if scope_type == "relationship":
            refs = normalized_scope_key.split("|")
            if len(refs) < 2 or len(refs) != len(set(refs)):
                raise ValueError("world_state relationship scope is invalid.")
            if "self" in refs:
                expected_refs = ["self", *sorted(ref for ref in refs if ref != "self")]
            else:
                expected_refs = sorted(refs)
            if refs != expected_refs:
                raise ValueError("world_state relationship scope must be normalized.")
            return "relationship", normalized_scope_key
        raise ValueError("world_state scope_type is invalid.")

    def _world_state_score_from_hint(self, hint: Any) -> float:
        if not isinstance(hint, str) or hint.strip() not in WORLD_STATE_HINT_SCORES:
            raise ValueError("world_state hint score is invalid.")
        return WORLD_STATE_HINT_SCORES[hint.strip()]

    def _empty_world_state_trace(
        self,
        *,
        source_kind: str | None,
        source_ref: str | None,
        foreground_world_state: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "result_status": "not_requested",
            "candidate_state_count": 0,
            "input_world_state_count": len(foreground_world_state),
            "previous_foreground_world_state": foreground_world_state,
            "foreground_world_state": foreground_world_state,
            "updated_state_count": 0,
            "replaced_state_count": 0,
            "expired_state_count": 0,
            "dropped_state_count": 0,
            "source_kind": source_kind,
            "source_ref": source_ref,
            "source_pack_contexts": {},
            "source_pack_state_type_hooks": {},
            "normalized_candidate_policies": [],
            "failure_reason": None,
        }

    def _should_consolidate_spontaneous_cycle(
        self,
        *,
        trigger_kind: str,
        pipeline: dict[str, Any],
        observation_summary: dict[str, Any] | None,
    ) -> bool:
        if trigger_kind not in {"wake", "background_wake", "desktop_watch", "capability_result"}:
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

    def _foreground_world_state_changed(self, pipeline: dict[str, Any]) -> bool:
        if not isinstance(pipeline, dict):
            return False
        world_state_trace = pipeline.get("world_state_trace")
        if not isinstance(world_state_trace, dict):
            return False
        previous = world_state_trace.get("previous_foreground_world_state") or []
        current = pipeline.get("foreground_world_state") or world_state_trace.get("foreground_world_state") or []
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
            "event_evidence_generation": {
                "requested_event_count": int(event_evidence_generation.get("requested_event_count", 0)),
                "succeeded_event_count": int(event_evidence_generation.get("succeeded_event_count", 0)),
                "failed_count": len(event_evidence_generation.get("failed_items", [])),
            },
            "recall_pack_selection": {
                "result_status": str(recall_pack_selection.get("result_status", "succeeded")),
                "selected_section_order": recall_pack_selection.get("selected_section_order", []),
                "selected_candidate_count": len(recall_pack_selection.get("selected_candidate_refs", [])),
                "dropped_candidate_count": len(recall_pack_selection.get("dropped_candidate_refs", [])),
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
        client_context: dict[str, Any],
        runtime_summary: dict[str, Any],
        foreground_world_state: list[dict[str, Any]] | None,
        recall_trace: dict[str, Any],
        decision_trace: dict[str, Any],
        world_state_trace: dict[str, Any] | None,
        result_trace: dict[str, Any],
        memory_trace: dict[str, Any] | None,
        pending_intent_selection: dict[str, Any] | None = None,
        observation_summary: dict[str, Any] | None = None,
        ongoing_action_summary: dict[str, Any] | None = None,
        initiative_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        input_trace = {
            "trigger_kind": cycle_summary["trigger_kind"],
            "input_summary": self._clamp(input_text),
            "client_context_summary": self._clamp(str(client_context)),
            "normalized_input_summary": self._clamp(input_text.strip()),
            "runtime_state_summary": runtime_summary,
            "pending_intent_selection": pending_intent_selection or self._empty_pending_intent_selection_trace(),
        }
        if foreground_world_state:
            input_trace["foreground_world_state"] = foreground_world_state
        if isinstance(observation_summary, dict):
            input_trace["observation_summary"] = observation_summary
        if isinstance(ongoing_action_summary, dict):
            input_trace["ongoing_action_summary"] = ongoing_action_summary
        if isinstance(initiative_context, dict):
            input_trace["initiative_context"] = initiative_context
        return {
            "cycle_id": cycle_id,
            "cycle_summary": cycle_summary,
            "input_trace": input_trace,
            "recall_trace": recall_trace,
            "decision_trace": decision_trace,
            "world_state_trace": world_state_trace or {},
            "result_trace": result_trace,
            "memory_trace": memory_trace or {},
        }

    def _build_success_recall_trace(self, recall_hint: dict[str, Any], recall_pack: dict[str, Any]) -> dict[str, Any]:
        recall_pack_summary = self._summarize_recall_pack(recall_pack)
        return {
            "recall_hint_summary": recall_hint,
            "candidate_count": recall_pack["candidate_count"],
            "selected_memory_unit_ids": recall_pack["selected_memory_ids"],
            "selected_episode_ids": recall_pack["selected_episode_ids"],
            "selected_event_ids": recall_pack["selected_event_ids"],
            "event_evidence_generation": recall_pack.get(
                "event_evidence_generation",
                self._empty_event_evidence_generation_trace(),
            ),
            "recall_pack_selection": recall_pack.get(
                "recall_pack_selection",
                self._empty_recall_pack_selection_trace(),
            ),
            "recall_pack_summary": recall_pack_summary,
            "adopted_reason_summary": self._recall_adopted_reason_summary(recall_pack),
            "rejected_candidate_summary": self._recall_rejected_reason_summary(recall_pack),
        }

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
            "recall_pack_selection": recall_pack_selection or self._empty_recall_pack_selection_trace(),
            "recall_pack_summary": None,
            "adopted_reason_summary": None,
            "rejected_candidate_summary": None,
        }

    def _build_success_decision_trace(
        self,
        *,
        state: dict[str, Any],
        input_text: str,
        time_context: dict[str, Any],
        affect_context: dict[str, Any],
        drive_state_summary: list[dict[str, Any]] | None,
        foreground_world_state: list[dict[str, Any]] | None,
        ongoing_action_summary: dict[str, Any] | None,
        capability_decision_view: list[dict[str, Any]] | None,
        initiative_context: dict[str, Any] | None,
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
                "initiative_context": initiative_context,
                "recall_pack_summary": self._summarize_recall_pack(recall_pack),
            },
            "primary_candidate_kind": decision["kind"],
            "pending_intent_candidate_summary": pending_intent_summary,
            "capability_request_candidate_summary": self._decision_capability_request_summary(decision),
        }
        if drive_state_summary:
            trace["drive_state_summary"] = drive_state_summary
        if isinstance(ongoing_action_summary, dict):
            trace["ongoing_action_summary"] = ongoing_action_summary
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

    def _build_failure_decision_trace(
        self,
        *,
        state: dict[str, Any],
        input_text: str,
        failure_reason: str,
        drive_state_summary: list[dict[str, Any]] | None = None,
        ongoing_action_summary: dict[str, Any] | None = None,
        capability_decision_view: list[dict[str, Any]] | None = None,
        initiative_context: dict[str, Any] | None = None,
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
                "initiative_context": initiative_context,
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
        initiative_context: dict[str, Any] | None = None,
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
        initiative_context: dict[str, Any] | None = None,
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
        initiative_context: dict[str, Any] | None,
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
        if trigger_kind in {"wake", "background_wake", "desktop_watch"}:
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
        initiative_context: dict[str, Any] | None,
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
        if trigger_kind in {"wake", "background_wake", "desktop_watch"}:
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
        initiative_context: dict[str, Any] | None,
        pending_intent_selection: dict[str, Any] | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if isinstance(initiative_context, dict):
            opportunity_summary = initiative_context.get("opportunity_summary")
            if isinstance(opportunity_summary, str) and opportunity_summary.strip():
                payload["opportunity_summary"] = self._clamp(opportunity_summary.strip(), limit=160)
            compact_pending_intent_summaries = self._compact_initiative_pending_intent_summaries(
                initiative_context.get("pending_intent_summaries")
            )
            if compact_pending_intent_summaries:
                payload["pending_intent_summaries"] = compact_pending_intent_summaries
            compact_world_state_summaries = self._compact_initiative_world_state_summaries(
                initiative_context.get("world_state_summary")
            )
            if compact_world_state_summaries:
                payload["world_state_summaries"] = compact_world_state_summaries
            intervention_risk_summary = initiative_context.get("intervention_risk_summary")
            if isinstance(intervention_risk_summary, str) and intervention_risk_summary.strip():
                payload["intervention_risk_summary"] = self._clamp(intervention_risk_summary.strip(), limit=160)
        compact_pending_intent_selection = self._compact_pending_intent_selection_summary(
            pending_intent_selection
        )
        if isinstance(compact_pending_intent_selection, dict):
            payload["pending_intent_selection_summary"] = compact_pending_intent_selection
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
        initiative_context: dict[str, Any] | None,
        world_state_trace: dict[str, Any] | None,
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
            client_context=client_context,
            runtime_summary=runtime_summary,
            foreground_world_state=foreground_world_state,
            recall_trace=self._build_success_recall_trace(recall_hint, recall_pack),
            decision_trace=self._build_success_decision_trace(
                state=state,
                input_text=input_text,
                time_context=time_context,
                affect_context=affect_context,
                drive_state_summary=drive_state_summary,
                foreground_world_state=foreground_world_state,
                ongoing_action_summary=ongoing_action_summary,
                capability_decision_view=capability_decision_view,
                initiative_context=initiative_context,
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
        initiative_context: dict[str, Any] | None = None,
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
            world_state_trace={},
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
