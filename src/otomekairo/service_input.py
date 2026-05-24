from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from typing import Any

from otomekairo.capabilities import (
    capability_manifests,
    capability_readiness_result_digest,
    capability_readiness_world_state_digest,
    capability_world_state_type,
)
from otomekairo.llm_contexts import InitiativeContext
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
from otomekairo.service_input_constants import (
    RECALL_HINT_RECENT_TURN_LIMIT,
    VISUAL_OBSERVATION_DATA_URI_PREFIX,
    VISUAL_OBSERVATION_IMAGE_LIMIT,
    WORLD_STATE_FOREGROUND_LIMIT,
)
from otomekairo.world_state_models import WorldStateTrace
from otomekairo.service_input_cycle import ServiceInputCycleMixin
from otomekairo.service_input_initiative import ServiceInputInitiativeMixin
from otomekairo.service_input_pipeline import ServiceInputPipelineMixin
from otomekairo.service_input_world_state import ServiceInputWorldStateMixin


class ServiceInputMixin(
    ServiceInputCycleMixin,
    ServiceInputPipelineMixin,
    ServiceInputInitiativeMixin,
    ServiceInputWorldStateMixin,
):

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
        if len(images) > VISUAL_OBSERVATION_IMAGE_LIMIT:
            raise ServiceError(
                400,
                "invalid_images",
                f"images must contain at most {VISUAL_OBSERVATION_IMAGE_LIMIT} item.",
            )
        normalized_images: list[str] = []
        for image in images:
            if not isinstance(image, str) or not image.strip():
                raise ServiceError(400, "invalid_images", "images must contain non-empty strings.")
            normalized_image = image.strip()
            if not self._is_image_data_uri(normalized_image):
                raise ServiceError(400, "invalid_images", "images must contain image data URIs.")
            normalized_images.append(normalized_image)
        return normalized_images

    def _is_image_data_uri(self, value: str) -> bool:
        if not value.startswith(VISUAL_OBSERVATION_DATA_URI_PREFIX):
            return False
        header, separator, body = value.partition(",")
        if separator != "," or not body.strip():
            return False
        return ";base64" in header

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
        source_pack = self._build_visual_observation_source_pack(
            started_at=started_at,
            input_text=input_text,
            trigger_kind=trigger_kind,
            client_context=client_context,
            observation_summary=observation_summary,
        )

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
        capability_id = enriched_observation_summary.get("capability_id")
        readiness_digest = (
            capability_readiness_result_digest(capability_id, enriched_observation_summary)
            if isinstance(capability_id, str)
            else None
        )
        if isinstance(readiness_digest, dict):
            enriched_observation_summary["readiness_digest"] = readiness_digest
        return enriched_client_context, enriched_observation_summary

    def _build_visual_observation_source_pack(
        self,
        *,
        started_at: str,
        input_text: str,
        trigger_kind: str,
        client_context: dict[str, Any],
        observation_summary: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "trigger_kind": trigger_kind,
            "image_input_kind": self._visual_observation_input_kind(
                trigger_kind=trigger_kind,
                observation_summary=observation_summary,
            ),
            "time_context": llm_local_time_text(started_at).replace("\n", " / "),
            "client_context": self._build_visual_observation_client_context(
                trigger_kind=trigger_kind,
                client_context=client_context,
                observation_summary=observation_summary,
            ),
            "observation_summary": self._build_visual_observation_observation_summary(observation_summary),
            "current_input_summary": self._clamp(input_text.strip(), limit=200) or "",
        }

    def _visual_observation_input_kind(
        self,
        *,
        trigger_kind: str,
        observation_summary: dict[str, Any],
    ) -> str:
        image_input_kind = observation_summary.get("image_input_kind")
        if isinstance(image_input_kind, str) and image_input_kind.strip():
            return image_input_kind.strip()
        if trigger_kind == "capability_result" and observation_summary.get("capability_id") == "vision.capture":
            return "vision_capture_result"
        return "conversation_attachment"

    def _build_visual_observation_client_context(
        self,
        *,
        trigger_kind: str,
        client_context: dict[str, Any],
        observation_summary: dict[str, Any],
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        include_foreground_hint = (
            trigger_kind == "capability_result"
            and observation_summary.get("capability_id") == "vision.capture"
        )
        for key, limit in (
            ("source", 48),
            ("client_id", 80),
            ("vision_source_id", 96),
            ("source_kind", 32),
            ("source_label", 80),
            ("active_app", 80),
            ("window_title", 120),
            ("locale", 32),
        ):
            if key in {"vision_source_id", "source_kind", "source_label", "active_app", "window_title", "locale"} and not include_foreground_hint:
                continue
            value = client_context.get(key)
            if isinstance(value, str) and value.strip():
                payload[key] = self._clamp(value.strip(), limit=limit)
        return payload

    def _build_visual_observation_observation_summary(
        self,
        observation_summary: dict[str, Any],
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for key in (
            "source",
            "image_input_kind",
            "capability_id",
            "vision_source_id",
            "source_kind",
            "source_label",
            "image_count",
            "image_interpreted",
            "visual_confidence_hint",
            "error",
        ):
            value = observation_summary.get(key)
            if value is None:
                continue
            payload[key] = value
        return payload

    def _pipeline_augmented_query_text(
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
        label = (
            "conversation_attachment_visual_summary"
            if self._observation_summary_is_conversation_attachment(observation_summary)
            else "visual_summary"
        )
        visual_input_summary = (
            f"<<<OTOMEKAIRO_INTERNAL_CONTEXT {label}>>>\n"
            f"{visual_summary_text}\n"
            "<<<END_OTOMEKAIRO_INTERNAL_CONTEXT>>>"
        )
        if not normalized_input_text:
            return visual_input_summary
        return f"{normalized_input_text}\n\n{visual_input_summary}"

    def _observation_summary_is_conversation_attachment(self, observation_summary: dict[str, Any] | None) -> bool:
        if not isinstance(observation_summary, dict):
            return False
        return observation_summary.get("source") == "conversation_attachment"

    def _visual_observation_summary_text(self, observation_summary: dict[str, Any] | None) -> str | None:
        if not isinstance(observation_summary, dict):
            return None
        summary_text = observation_summary.get("visual_summary_text")
        if not isinstance(summary_text, str) or not summary_text.strip():
            return None
        return summary_text.strip()

    def _annotate_capability_decision_view_with_fresh_world_state(
        self,
        *,
        capability_decision_view: list[dict[str, Any]] | None,
        foreground_world_state: list[dict[str, Any]] | None,
        world_state_trace: WorldStateTrace | None,
        trigger_kind: str,
    ) -> list[dict[str, Any]] | None:
        if not capability_decision_view:
            return capability_decision_view
        if trigger_kind == "user_message":
            return capability_decision_view
        reuse_world_state = self._foreground_world_state_for_capability_reuse(
            foreground_world_state=foreground_world_state,
            world_state_trace=world_state_trace,
            trigger_kind=trigger_kind,
        )
        if not reuse_world_state:
            return capability_decision_view
        fresh_world_states = self._fresh_foreground_world_state_summaries(reuse_world_state)
        fresh_state_by_type = self._fresh_foreground_world_state_by_type(fresh_world_states)
        if not fresh_state_by_type:
            return capability_decision_view

        annotated: list[dict[str, Any]] = []
        changed = False
        for item in capability_decision_view:
            if not isinstance(item, dict):
                annotated.append(item)
                continue
            capability_id = item.get("id")
            state_type = (
                self._capability_fresh_world_state_type(capability_id)
                if isinstance(capability_id, str)
                else None
            )
            if capability_id == "vision.capture":
                if item.get("available") is True:
                    fresh_visual_sources = self._fresh_visual_world_states_for_sources(
                        vision_sources=item.get("vision_sources"),
                        fresh_world_states=fresh_world_states,
                    )
                    if fresh_visual_sources:
                        annotated.append(
                            {
                                **item,
                                "fresh_world_state_by_vision_source": fresh_visual_sources,
                                "fresh_world_state_policy": "明示的なユーザー依頼なしでは同じ vision_source_id の現在状態を再取得しない。",
                            }
                        )
                        changed = True
                    else:
                        annotated.append(item)
                else:
                    annotated.append(item)
                continue
            fresh_state = fresh_state_by_type.get(state_type) if state_type is not None else None
            if item.get("available") is not True or fresh_state is None:
                annotated.append(item)
                continue
            readiness_digest = capability_readiness_world_state_digest(
                capability_id,
                fresh_state.get("state_type"),
            )
            annotated_item = {
                **item,
                "fresh_world_state_available": True,
                "fresh_world_state": fresh_state,
                "fresh_world_state_policy": "明示的なユーザー依頼なしでは同じ現在状態を再取得しない。",
            }
            if isinstance(readiness_digest, dict):
                annotated_item["fresh_world_state_readiness_digest"] = readiness_digest
            annotated.append(annotated_item)
            changed = True
        return annotated if changed else capability_decision_view

    def _foreground_world_state_for_capability_reuse(
        self,
        *,
        foreground_world_state: list[dict[str, Any]] | None,
        world_state_trace: WorldStateTrace | None,
        trigger_kind: str,
    ) -> list[dict[str, Any]]:
        if trigger_kind == "capability_result":
            return foreground_world_state or []
        previous = world_state_trace.previous_foreground_world_state if world_state_trace is not None else None
        if isinstance(previous, list):
            return [item for item in previous if isinstance(item, dict)]
        return []

    def _fresh_foreground_world_state_summaries(
        self,
        foreground_world_state: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        fresh_states: list[dict[str, Any]] = []
        for summary in foreground_world_state:
            if not isinstance(summary, dict) or not self._foreground_world_state_is_fresh(summary):
                continue
            state_type = summary.get("state_type")
            if not isinstance(state_type, str) or not state_type.strip():
                continue
            summary_text = summary.get("summary_text")
            if not isinstance(summary_text, str) or not summary_text.strip():
                continue
            compact_summary = {
                "state_type": state_type.strip(),
                "scope": summary.get("scope"),
                "summary_text": self._clamp(summary_text.strip(), limit=120),
                "age_label": summary.get("age_label"),
                "confidence": summary.get("confidence"),
                "salience": summary.get("salience"),
                "integration_key": summary.get("integration_key"),
            }
            fresh_states.append(compact_summary)
        return fresh_states

    def _fresh_foreground_world_state_by_type(
        self,
        fresh_world_states: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        fresh_state_by_type: dict[str, dict[str, Any]] = {}
        for compact_summary in fresh_world_states:
            state_type = compact_summary.get("state_type")
            if not isinstance(state_type, str) or not state_type.strip():
                continue
            existing = fresh_state_by_type.get(state_type.strip())
            if existing is None or (
                self._world_state_reuse_rank(compact_summary) > self._world_state_reuse_rank(existing)
            ):
                fresh_state_by_type[state_type.strip()] = compact_summary
        return fresh_state_by_type

    def _fresh_visual_world_states_for_sources(
        self,
        *,
        vision_sources: Any,
        fresh_world_states: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not isinstance(vision_sources, list):
            return []
        visual_states_by_key = {
            state.get("integration_key"): state
            for state in fresh_world_states
            if isinstance(state, dict)
            and state.get("state_type") == "visual_context"
            and isinstance(state.get("integration_key"), str)
        }
        matches: list[dict[str, Any]] = []
        for source in vision_sources:
            if not isinstance(source, dict):
                continue
            source_id = self._client_context_text(source.get("vision_source_id"), limit=96)
            if source_id is None:
                continue
            source_key = self._world_state_vision_source_key({"vision_source_id": source_id})
            if source_key is None:
                continue
            state = visual_states_by_key.get(f"visual_context:{source_key}")
            if not isinstance(state, dict):
                continue
            payload = {
                "vision_source_id": source_id,
                "summary_text": state.get("summary_text"),
                "age_label": state.get("age_label"),
                "confidence": state.get("confidence"),
                "salience": state.get("salience"),
            }
            label = self._client_context_text(source.get("label"), limit=80)
            if label is not None:
                payload["source_label"] = label
            matches.append(payload)
        return matches[:6]

    def _foreground_world_state_is_fresh(self, summary: dict[str, Any]) -> bool:
        age_label = summary.get("age_label")
        if age_label == "たった今":
            return True
        if not isinstance(age_label, str) or not age_label.endswith("分前"):
            return False
        minute_text = age_label[:-2]
        if not minute_text.isdigit():
            return False
        return int(minute_text) <= 5

    def _world_state_reuse_rank(self, summary: dict[str, Any]) -> float:
        salience = summary.get("salience")
        confidence = summary.get("confidence")
        score = 0.0
        if isinstance(salience, (int, float)):
            score += float(salience)
        if isinstance(confidence, (int, float)):
            score += float(confidence)
        if summary.get("age_label") == "たった今":
            score += 0.2
        return score

    def _capability_fresh_world_state_type(self, capability_id: str) -> str | None:
        return capability_world_state_type(capability_id)

    def _build_capability_result_decision_context(
        self,
        *,
        trigger_kind: str,
        observation_summary: dict[str, Any] | None,
        capability_request_summary: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if trigger_kind != "capability_result":
            return None
        source_capability_id = self._capability_result_source_capability_id(
            observation_summary=observation_summary,
            capability_request_summary=capability_request_summary,
        )
        if source_capability_id is None:
            return None
        payload: dict[str, Any] = {
            "source_capability_id": source_capability_id,
            "allowed_followup_capability_ids": [source_capability_id],
            "followup_policy_summary": (
                "source capability と異なる capability_request は出さず、"
                "受け取った result への reply / noop / pending_intent で閉じる。"
            ),
        }
        source_request_summary = self._compact_capability_request_summary(capability_request_summary)
        if isinstance(source_request_summary, dict):
            payload["source_request_summary"] = source_request_summary
        compact_observation_summary = self._compact_capability_followup_observation_summary(observation_summary)
        if isinstance(compact_observation_summary, dict):
            payload["observation_summary"] = compact_observation_summary
        return payload

    def _build_visual_observation_decision_context(
        self,
        *,
        trigger_kind: str,
        observation_summary: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if not isinstance(observation_summary, dict):
            return None
        summary_text = self._visual_observation_summary_text(observation_summary)
        if summary_text is None:
            return None

        if trigger_kind == "user_message" and observation_summary.get("source") == "conversation_attachment":
            source = "conversation_attachment"
            image_input_kind = "conversation_attachment"
        elif self._observation_summary_is_desktop_vision_capture(observation_summary):
            source = "vision_capture_result"
            image_input_kind = "vision_capture_result"
        else:
            return None

        payload: dict[str, Any] = {
            "source": source,
            "image_input_kind": image_input_kind,
            "image_interpreted": observation_summary.get("image_interpreted") is True,
            "visual_summary_text": self._clamp(summary_text, limit=240),
        }
        for key in ("image_count", "visual_confidence_hint", "vision_source_id", "source_kind", "source_label"):
            value = observation_summary.get(key)
            if value is not None:
                payload[key] = value
        if source == "vision_capture_result":
            payload["retention_policy"] = "ephemeral_decision_only"
        return payload

    def _observation_summary_is_desktop_vision_capture(
        self,
        observation_summary: dict[str, Any] | None,
    ) -> bool:
        if not isinstance(observation_summary, dict):
            return False
        source_kind = observation_summary.get("source_kind")
        return (
            observation_summary.get("source") == "capability_result"
            and observation_summary.get("capability_id") == "vision.capture"
            and isinstance(source_kind, str)
            and source_kind.strip() == "desktop"
        )

    def _capability_result_source_capability_id(
        self,
        *,
        observation_summary: dict[str, Any] | None,
        capability_request_summary: dict[str, Any] | None,
    ) -> str | None:
        for value in (
            observation_summary.get("capability_id") if isinstance(observation_summary, dict) else None,
            capability_request_summary.get("capability_id")
            if isinstance(capability_request_summary, dict)
            else None,
        ):
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None


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
    ) -> tuple[dict[str, Any], str, dict[str, Any]]:
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
            return (
                self._noop_pipeline(state=state, started_at=started_at, reason_summary=due["reason_summary"]),
                input_text,
                client_context,
            )

        # 定期観測
        client_context = self._run_wake_policy_observations(
            state=state,
            started_at=started_at,
            client_context=client_context,
            cycle_id=cycle_id,
        )
        input_text = self._build_wake_input_text(
            state=state,
            client_context=client_context,
            selected_candidate=selected_candidate,
        )

        # クールダウン
        cooldown_reason = self._wake_cooldown_reason(current_time=started_at)
        if cooldown_reason is not None and not self._client_context_has_judgable_desktop_observation(client_context):
            self._set_last_wake_at(started_at)
            debug_log("Wake", f"{cycle_label} skipped cooldown={self._clamp(cooldown_reason)}")
            return (
                self._noop_pipeline(state=state, started_at=started_at, reason_summary=cooldown_reason),
                input_text,
                client_context,
            )
        if cooldown_reason is not None:
            debug_log("Wake", f"{cycle_label} cooldown judged desktop_observation={self._clamp(cooldown_reason)}")

        # 候補
        if selected_candidate is None:
            self._set_last_wake_at(started_at)
            if not self._has_autonomous_initiative_context(
                state=state,
                current_time=started_at,
                client_context=client_context,
            ):
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
                    client_context,
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
                    client_context,
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
        return pipeline, input_text, client_context

    def _run_wake_policy_observations(
        self,
        *,
        state: dict[str, Any],
        started_at: str,
        client_context: dict[str, Any],
        cycle_id: str | None,
    ) -> dict[str, Any]:
        observations = self._enabled_wake_policy_observations(state)
        if not observations:
            return client_context

        cycle_label = self._debug_cycle_label(cycle_id)
        debug_log("Wake", f"{cycle_label} observations start count={len(observations)}")
        summaries: list[dict[str, Any]] = []
        for observation in observations:
            summary = self._run_wake_policy_observation(
                state=state,
                started_at=started_at,
                observation=observation,
                cycle_id=cycle_id,
            )
            summary = self._record_wake_policy_observation_runtime_state(
                summary=summary,
                current_time=started_at,
            )
            summaries.append(summary)
        summary_text = self._wake_policy_observation_summary_text(summaries)
        debug_log("Wake", f"{cycle_label} observations done summary={self._clamp(summary_text)}")
        next_context = {
            **client_context,
            "wake_observations": summaries,
            "wake_observation_summary": summary_text,
        }
        desktop_signal = self._wake_policy_desktop_observation_signal(summaries)
        if desktop_signal:
            next_context["desktop_observation_signal"] = desktop_signal
        return next_context

    def _enabled_wake_policy_observations(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        wake_policy = state.get("wake_policy")
        if not isinstance(wake_policy, dict) or wake_policy.get("mode") != "interval":
            return []
        observations = wake_policy.get("observations")
        if not isinstance(observations, list):
            return []
        return [
            observation
            for observation in observations
            if isinstance(observation, dict) and observation.get("enabled") is True
        ]

    def _run_wake_policy_observation(
        self,
        *,
        state: dict[str, Any],
        started_at: str,
        observation: dict[str, Any],
        cycle_id: str | None,
    ) -> dict[str, Any]:
        observation_id = self._client_context_text(observation.get("observation_id"), limit=96) or "observation:unknown"
        capability_id = self._client_context_text(observation.get("capability_id"), limit=80) or "unknown"
        input_payload = observation.get("input")
        if not isinstance(input_payload, dict):
            return self._wake_policy_observation_failure_summary(
                observation=observation,
                reason_summary="wake_policy observation input が不正。",
            )
        if not self._wake_policy_observation_source_available(capability_id=capability_id, input_payload=input_payload):
            return self._wake_policy_observation_failure_summary(
                observation=observation,
                reason_summary="対象 vision source が接続されていない。",
            )

        try:
            capability_response = self._dispatch_capability_request(
                memory_set_id=state["selected_memory_set_id"],
                capability_id=capability_id,
                input_payload=input_payload,
                current_time=self._now_iso(),
                goal_summary=f"wake_policy observation {observation_id}",
                wait_for_response=True,
                component="WakeObservation",
            )
        except CapabilityDispatchError as exc:
            return self._wake_policy_observation_failure_summary(
                observation=observation,
                reason_summary=str(exc),
                capability_request_summary=exc.capability_request_summary,
            )
        except ValueError as exc:
            return self._wake_policy_observation_failure_summary(
                observation=observation,
                reason_summary=str(exc),
            )
        if not isinstance(capability_response, dict):
            return self._wake_policy_observation_failure_summary(
                observation=observation,
                reason_summary="capability response が空。",
            )
        return self._apply_wake_policy_observation_result(
            state=state,
            started_at=started_at,
            observation=observation,
            capability_response=capability_response,
            cycle_id=cycle_id,
        )

    def _wake_policy_observation_source_available(self, *, capability_id: str, input_payload: dict[str, Any]) -> bool:
        if capability_id != "vision.capture":
            return True
        vision_source_id = input_payload.get("vision_source_id")
        if not isinstance(vision_source_id, str) or not vision_source_id.strip():
            return False
        return isinstance(self._event_stream_registry.get_vision_source(vision_source_id.strip()), dict)

    def _apply_wake_policy_observation_result(
        self,
        *,
        state: dict[str, Any],
        started_at: str,
        observation: dict[str, Any],
        capability_response: dict[str, Any],
        cycle_id: str | None,
    ) -> dict[str, Any]:
        capability_id = self._capability_result_capability_id(capability_response)
        request_record = capability_response.get("request_record")
        capability_request_summary = self._capability_request_summary(request_record)
        client_context = self._build_capability_result_client_context(capability_response)
        observation_summary = self._capability_result_observation_summary(capability_response)
        input_text = self._build_capability_result_input_text(
            client_context=client_context,
            capability_response=capability_response,
        )
        try:
            client_context, observation_summary, input_text = self._prepare_capability_result_context(
                state=state,
                started_at=started_at,
                capability_id=capability_id,
                client_context=client_context,
                observation_summary=observation_summary,
                input_text=input_text,
                capability_response=capability_response,
            )
            self._refresh_world_state_context(
                state=state,
                started_at=started_at,
                input_text=input_text,
                trigger_kind="capability_result",
                client_context=client_context,
                cycle_id=cycle_id,
                selected_candidate=None,
                observation_summary=observation_summary,
                capability_request_summary=capability_request_summary,
            )
            transition_summary = self._finish_wake_policy_observation_ongoing_action(
                request_record=request_record,
                current_time=self._now_iso(),
                capability_id=capability_id,
                capability_response=capability_response,
                observation_summary=observation_summary,
                failure_reason=None,
            )
            self._apply_capability_runtime_state_followup(
                capability_id=capability_id,
                current_time=self._now_iso(),
                observation_summary=observation_summary,
                result_payload=capability_response,
                ongoing_action_transition_summary=transition_summary,
            )
            return self._wake_policy_observation_success_summary(
                observation=observation,
                capability_response=capability_response,
                observation_summary=observation_summary,
                capability_request_summary=capability_request_summary,
            )
        except (LLMError, KeyError, TypeError, ValueError) as exc:
            transition_summary = self._finish_wake_policy_observation_ongoing_action(
                request_record=request_record,
                current_time=self._now_iso(),
                capability_id=capability_id,
                capability_response=capability_response,
                observation_summary=observation_summary,
                failure_reason=str(exc),
            )
            self._apply_capability_runtime_state_followup(
                capability_id=capability_id,
                current_time=self._now_iso(),
                observation_summary=observation_summary,
                result_payload=capability_response,
                ongoing_action_transition_summary=transition_summary,
                failure_reason=str(exc),
            )
            return self._wake_policy_observation_failure_summary(
                observation=observation,
                reason_summary=str(exc),
                capability_request_summary=capability_request_summary,
            )

    def _finish_wake_policy_observation_ongoing_action(
        self,
        *,
        request_record: Any,
        current_time: str,
        capability_id: str,
        capability_response: dict[str, Any],
        observation_summary: dict[str, Any] | None,
        failure_reason: str | None,
    ) -> dict[str, Any] | None:
        result_error = capability_response.get("error") not in {None, ""} or failure_reason is not None
        terminal_kind = "interrupted" if result_error else "completed"
        terminal_reason = (
            "wake_policy observation の取得または反映に失敗した。"
            if result_error
            else "wake_policy observation の取得結果を判断材料へ反映した。"
        )
        if result_error:
            final_step_summary = "wake_policy observation を中断した。"
        elif self._observation_summary_is_desktop_vision_capture(observation_summary):
            final_step_summary = "desktop wake observation の結果を一時観測として判断材料へ反映した。"
        else:
            final_step_summary = "wake_policy observation の結果を world_state へ反映した。"
        detail_summary = failure_reason or self._capability_result_followup_hint_summary(
            capability_id=capability_id,
            observation_summary=observation_summary,
            result_payload=capability_response,
        )
        return self._finish_capability_ongoing_action(
            request_record=request_record,
            current_time=current_time,
            terminal_kind=terminal_kind,
            reason_code="wake_observation_result",
            terminal_reason=terminal_reason,
            final_step_summary=final_step_summary,
            transition_source="wake_policy_observation",
            result_error=result_error,
            detail_summary=detail_summary,
        )

    def _wake_policy_observation_success_summary(
        self,
        *,
        observation: dict[str, Any],
        capability_response: dict[str, Any],
        observation_summary: dict[str, Any],
        capability_request_summary: dict[str, Any] | None,
    ) -> dict[str, Any]:
        payload = self._wake_policy_observation_base_summary(observation)
        error = observation_summary.get("error")
        has_error = isinstance(error, str) and error.strip()
        payload["status"] = "failed" if has_error else "succeeded"
        if has_error:
            payload["reason_summary"] = self._clamp(error.strip(), limit=160)
        request_id = capability_response.get("request_id")
        if isinstance(request_id, str) and request_id.strip():
            payload["request_id"] = request_id.strip()
        for key in (
            "vision_source_id",
            "source_kind",
            "source_label",
            "active_app",
            "window_title",
            "visual_summary_text",
            "error",
        ):
            value = observation_summary.get(key)
            if isinstance(value, str) and value.strip():
                payload[key] = self._clamp(value.strip(), limit=160)
        image_count = observation_summary.get("image_count")
        if isinstance(image_count, int):
            payload["image_count"] = image_count
        if isinstance(capability_request_summary, dict):
            payload["capability_request_summary"] = capability_request_summary
        return payload

    def _wake_policy_observation_failure_summary(
        self,
        *,
        observation: dict[str, Any],
        reason_summary: str,
        capability_request_summary: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = self._wake_policy_observation_base_summary(observation)
        payload["status"] = "failed"
        payload["reason_summary"] = self._clamp(reason_summary, limit=160)
        if isinstance(capability_request_summary, dict):
            payload["capability_request_summary"] = capability_request_summary
        return payload

    def _wake_policy_observation_base_summary(self, observation: dict[str, Any]) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for key, limit in (
            ("observation_id", 96),
            ("capability_id", 80),
        ):
            value = self._client_context_text(observation.get(key), limit=limit)
            if value is not None:
                payload[key] = value
        input_payload = observation.get("input")
        if isinstance(input_payload, dict):
            vision_source_id = self._client_context_text(input_payload.get("vision_source_id"), limit=96)
            if vision_source_id is not None:
                payload["vision_source_id"] = vision_source_id
        return payload

    def _wake_policy_observation_summary_text(self, summaries: list[dict[str, Any]]) -> str:
        if not summaries:
            return "定期観測対象は無い。"
        parts: list[str] = []
        for summary in summaries[:6]:
            label = self._client_context_text(summary.get("source_label"), limit=80)
            if label is None:
                label = self._client_context_text(summary.get("vision_source_id"), limit=96)
            if label is None:
                label = self._client_context_text(summary.get("observation_id"), limit=96) or "unknown"
            if summary.get("status") == "succeeded":
                text = self._client_context_text(summary.get("visual_summary_text"), limit=120)
                if text is None:
                    text = "取得済み"
                parts.append(f"{label}: {text}")
                continue
            reason = self._client_context_text(summary.get("reason_summary"), limit=120) or "取得失敗"
            parts.append(f"{label}: failed {reason}")
        return self._clamp(" / ".join(parts), limit=360) or "定期観測結果は空。"

    def _record_wake_policy_observation_runtime_state(
        self,
        *,
        summary: dict[str, Any],
        current_time: str,
    ) -> dict[str, Any]:
        observation_id = summary.get("observation_id")
        if not isinstance(observation_id, str) or not observation_id.strip():
            return summary
        status = summary.get("status")
        if not isinstance(status, str) or not status.strip():
            return summary
        normalized_observation_id = observation_id.strip()
        with self._runtime_state_lock:
            previous_runtime = dict(self._wake_observation_runtime_state.get(normalized_observation_id, {}))

        enriched_summary = dict(summary)
        desktop_signal = self._build_desktop_observation_signal(
            summary=summary,
            previous_runtime=previous_runtime,
            current_time=current_time,
        )
        if desktop_signal:
            enriched_summary["desktop_observation_signal"] = desktop_signal
            for key in ("novelty_kind", "reply_eligibility", "scene_signature"):
                value = desktop_signal.get(key)
                if isinstance(value, str) and value.strip():
                    enriched_summary[key] = value.strip()

        last_summary = self._client_context_text(summary.get("visual_summary_text"), limit=160)
        if last_summary is None and status == "succeeded":
            last_summary = self._client_context_text(summary.get("source_label"), limit=80) or "取得済み"
        last_error = self._client_context_text(summary.get("reason_summary"), limit=160)
        if last_error is None:
            last_error = self._client_context_text(summary.get("error"), limit=160)
        payload: dict[str, Any] = {
            "observation_id": normalized_observation_id,
            "last_run_at": current_time,
            "last_status": status.strip(),
            "last_summary": last_summary,
            "last_error": last_error,
            "last_request_id": summary.get("request_id") if isinstance(summary.get("request_id"), str) else None,
            "last_vision_source_id": summary.get("vision_source_id") if isinstance(summary.get("vision_source_id"), str) else None,
            "last_source_label": summary.get("source_label") if isinstance(summary.get("source_label"), str) else None,
            "last_active_app": summary.get("active_app") if isinstance(summary.get("active_app"), str) else None,
            "last_window_title": summary.get("window_title") if isinstance(summary.get("window_title"), str) else None,
            "last_image_count": summary.get("image_count") if isinstance(summary.get("image_count"), int) else None,
        }
        self._apply_desktop_observation_runtime_payload(
            payload=payload,
            summary=enriched_summary,
            previous_runtime=previous_runtime,
            current_time=current_time,
        )
        with self._runtime_state_lock:
            self._wake_observation_runtime_state[normalized_observation_id] = payload
        return enriched_summary

    def _build_desktop_observation_signal(
        self,
        *,
        summary: dict[str, Any],
        previous_runtime: dict[str, Any],
        current_time: str,
    ) -> dict[str, Any] | None:
        if not self._wake_observation_is_desktop_vision_capture(summary):
            return None
        scene_signature = self._desktop_observation_scene_signature(summary)
        if scene_signature is None:
            return None
        previous_signature = self._client_context_text(previous_runtime.get("last_scene_signature"), limit=320)
        pending_scene = previous_runtime.get("pending_novel_scene")
        pending_signature = None
        if isinstance(pending_scene, dict):
            pending_signature = self._client_context_text(pending_scene.get("scene_signature"), limit=320)
        last_prompted_signature = self._client_context_text(
            previous_runtime.get("last_prompted_scene_signature"),
            limit=320,
        )
        cooldown_reason = self._wake_cooldown_reason(current_time=current_time)
        cooldown_active = cooldown_reason is not None
        same_as_previous = self._desktop_scene_signatures_similar(scene_signature, previous_signature)
        same_as_pending = self._desktop_scene_signatures_similar(scene_signature, pending_signature)
        same_as_prompted = self._desktop_scene_signatures_similar(scene_signature, last_prompted_signature)

        if same_as_pending and not cooldown_active and not same_as_prompted:
            novelty_kind = "pending_after_cooldown"
        elif same_as_prompted:
            novelty_kind = "already_prompted"
        elif previous_signature is None:
            novelty_kind = "first_success"
        elif same_as_previous:
            novelty_kind = "same"
        else:
            novelty_kind = "changed"

        reply_eligible_novelty = novelty_kind in {"first_success", "changed", "pending_after_cooldown"}
        if same_as_prompted:
            reply_eligibility = "already_prompted"
        elif reply_eligible_novelty:
            reply_eligibility = "eligible"
        else:
            reply_eligibility = "not_needed"

        reason_summary = self._desktop_observation_signal_reason(
            novelty_kind=novelty_kind,
            reply_eligibility=reply_eligibility,
            cooldown_reason=cooldown_reason,
        )
        signal: dict[str, Any] = {
            "observation_id": summary.get("observation_id"),
            "novelty_kind": novelty_kind,
            "reply_eligibility": reply_eligibility,
            "reason_summary": reason_summary,
            "scene_signature": scene_signature,
            "summary_text": self._client_context_text(summary.get("visual_summary_text"), limit=160),
            "source_label": self._client_context_text(summary.get("source_label"), limit=80),
            "active_app": self._client_context_text(summary.get("active_app"), limit=80),
            "window_title": self._client_context_text(summary.get("window_title"), limit=120),
            "cooldown_active": cooldown_active,
        }
        if cooldown_reason is not None:
            signal["cooldown_reason"] = cooldown_reason
        return {key: value for key, value in signal.items() if value is not None}

    def _apply_desktop_observation_runtime_payload(
        self,
        *,
        payload: dict[str, Any],
        summary: dict[str, Any],
        previous_runtime: dict[str, Any],
        current_time: str,
    ) -> None:
        signal = summary.get("desktop_observation_signal")
        if not isinstance(signal, dict):
            return
        scene_signature = self._client_context_text(signal.get("scene_signature"), limit=320)
        if scene_signature is None:
            return
        previous_signature = self._client_context_text(previous_runtime.get("last_scene_signature"), limit=320)
        same_count = previous_runtime.get("same_scene_count")
        if not isinstance(same_count, int) or same_count < 0:
            same_count = 0
        payload["last_success_at"] = current_time
        payload["last_scene_signature"] = scene_signature
        if self._desktop_scene_signatures_similar(scene_signature, previous_signature):
            payload["same_scene_count"] = same_count + 1
        else:
            payload["same_scene_count"] = 1
        for key in ("last_prompted_scene_signature", "last_prompted_at"):
            value = previous_runtime.get(key)
            if isinstance(value, str) and value.strip():
                payload[key] = value.strip()

        pending_scene = previous_runtime.get("pending_novel_scene")
        if isinstance(pending_scene, dict):
            payload["pending_novel_scene"] = dict(pending_scene)
        reply_eligibility = signal.get("reply_eligibility")
        if (
            reply_eligibility == "eligible"
            and signal.get("cooldown_active") is True
            and signal.get("novelty_kind") in {"first_success", "changed"}
        ):
            first_seen_at = current_time
            if isinstance(pending_scene, dict) and self._desktop_scene_signatures_similar(
                scene_signature,
                self._client_context_text(pending_scene.get("scene_signature"), limit=320),
            ):
                previous_first_seen_at = pending_scene.get("first_seen_at")
                if isinstance(previous_first_seen_at, str) and previous_first_seen_at.strip():
                    first_seen_at = previous_first_seen_at.strip()
            payload["pending_novel_scene"] = {
                "scene_signature": scene_signature,
                "summary_text": signal.get("summary_text"),
                "first_seen_at": first_seen_at,
                "suppression_reason": "cooldown",
            }
        elif reply_eligibility in {"eligible", "already_prompted"}:
            payload.pop("pending_novel_scene", None)

    def _wake_observation_is_desktop_vision_capture(self, summary: dict[str, Any]) -> bool:
        return (
            summary.get("status") == "succeeded"
            and summary.get("capability_id") == "vision.capture"
            and isinstance(summary.get("source_kind"), str)
            and summary["source_kind"].strip() == "desktop"
        )

    def _desktop_observation_scene_signature(self, summary: dict[str, Any]) -> str | None:
        parts: list[str] = []
        for key in ("vision_source_id", "source_label", "active_app", "window_title", "visual_summary_text"):
            value = self._client_context_text(summary.get(key), limit=160)
            if value is not None:
                parts.append(f"{key}={value}")
        if not parts:
            return None
        normalized = " | ".join(" ".join(part.lower().split()) for part in parts)
        return self._clamp(normalized, limit=320)

    def _desktop_scene_signatures_similar(self, current: str | None, previous: str | None) -> bool:
        if current is None or previous is None:
            return False
        if current == previous:
            return True
        current_fields = self._desktop_scene_signature_fields(current)
        previous_fields = self._desktop_scene_signature_fields(previous)
        for key in ("vision_source_id", "active_app", "window_title"):
            current_value = current_fields.get(key)
            previous_value = previous_fields.get(key)
            if current_value and previous_value and current_value != previous_value:
                return False
        current_summary = current_fields.get("visual_summary_text") or current
        previous_summary = previous_fields.get("visual_summary_text") or previous
        return SequenceMatcher(None, current_summary, previous_summary).ratio() >= DESKTOP_SCENE_SIMILARITY_THRESHOLD

    def _desktop_scene_signature_fields(self, signature: str) -> dict[str, str]:
        fields: dict[str, str] = {}
        for part in signature.split(" | "):
            key, separator, value = part.partition("=")
            if separator and key and value:
                fields[key] = value
        return fields

    def _desktop_observation_signal_reason(
        self,
        *,
        novelty_kind: str,
        reply_eligibility: str,
        cooldown_reason: str | None,
    ) -> str:
        if reply_eligibility == "already_prompted":
            return "この desktop scene には既に自発 reply 済みなので、繰り返さない。"
        if reply_eligibility == "eligible" and cooldown_reason is not None and novelty_kind in {"first_success", "changed"}:
            return "cooldown 中だが desktop scene が変化したため、cooldown を割り込み量の調整材料として短い reply の候補にする。"
        if novelty_kind == "first_success":
            return "desktop wake observation の初回成功で、未発話の前景として扱う。"
        if novelty_kind == "changed":
            return "desktop wake observation が前回と変化し、未発話の前景として扱う。"
        if novelty_kind == "pending_after_cooldown":
            return "cooldown 中に保留した desktop scene がまだ見えており、今は短い reply の候補になる。"
        return "desktop scene は前回と大きく変わらないため、通常は見送る。"

    def _wake_policy_desktop_observation_signal(self, summaries: list[dict[str, Any]]) -> dict[str, Any] | None:
        for summary in summaries:
            if not isinstance(summary, dict):
                continue
            signal = summary.get("desktop_observation_signal")
            if isinstance(signal, dict) and signal:
                return dict(signal)
        return None

    def _compact_desktop_observation_signal(self, signal: Any) -> dict[str, Any] | None:
        if not isinstance(signal, dict):
            return None
        payload: dict[str, Any] = {}
        for key, limit in (
            ("observation_id", 96),
            ("novelty_kind", 48),
            ("reply_eligibility", 48),
            ("reason_summary", 180),
            ("summary_text", 160),
            ("source_label", 80),
            ("active_app", 80),
            ("window_title", 120),
        ):
            value = self._client_context_text(signal.get(key), limit=limit)
            if value is not None:
                payload[key] = value
        cooldown_active = signal.get("cooldown_active")
        if isinstance(cooldown_active, bool):
            payload["cooldown_active"] = cooldown_active
        return payload or None

    def _has_autonomous_initiative_context(
        self,
        *,
        state: dict[str, Any],
        current_time: str,
        client_context: dict[str, Any] | None = None,
    ) -> bool:
        if self._client_context_has_successful_wake_observation(client_context):
            return True
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

    def _client_context_has_successful_wake_observation(
        self,
        client_context: dict[str, Any] | None,
    ) -> bool:
        if not isinstance(client_context, dict):
            return False
        wake_observations = client_context.get("wake_observations")
        if not isinstance(wake_observations, list):
            return False
        desktop_signal = self._compact_desktop_observation_signal(
            client_context.get("desktop_observation_signal")
        )
        if desktop_signal:
            return self._desktop_observation_signal_is_judgable(desktop_signal)
        for item in wake_observations:
            if not isinstance(item, dict) or item.get("status") != "succeeded":
                continue
            if (
                item.get("capability_id") == "vision.capture"
                and isinstance(item.get("source_kind"), str)
                and item["source_kind"].strip() == "desktop"
            ):
                signal = self._compact_desktop_observation_signal(item.get("desktop_observation_signal"))
                return self._desktop_observation_signal_is_judgable(signal)
            summary_text = item.get("visual_summary_text")
            if isinstance(summary_text, str) and summary_text.strip():
                return True
            image_count = item.get("image_count")
            if isinstance(image_count, int) and image_count > 0:
                return True
        return False

    def _client_context_has_judgable_desktop_observation(
        self,
        client_context: dict[str, Any] | None,
    ) -> bool:
        if not isinstance(client_context, dict):
            return False
        signal = self._compact_desktop_observation_signal(
            client_context.get("desktop_observation_signal")
        )
        if self._desktop_observation_signal_is_judgable(signal):
            return True
        wake_observations = client_context.get("wake_observations")
        if not isinstance(wake_observations, list):
            return False
        for item in wake_observations:
            if not isinstance(item, dict):
                continue
            signal = self._compact_desktop_observation_signal(item.get("desktop_observation_signal"))
            if self._desktop_observation_signal_is_judgable(signal):
                return True
        return False

    def _desktop_observation_signal_is_judgable(self, signal: dict[str, Any] | None) -> bool:
        if not isinstance(signal, dict):
            return False
        return signal.get("reply_eligibility") in {
            "eligible",
        }

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
        evidence_pack = recall_pack.get("evidence_pack")
        # 要約
        summary = {
            "self_model": len(recall_pack["self_model"]),
            "user_model": len(recall_pack["user_model"]),
            "relationship_model": len(recall_pack["relationship_model"]),
            "active_topics": len(recall_pack["active_topics"]),
            "active_commitments": len(recall_pack["active_commitments"]),
            "episodic_evidence": len(recall_pack["episodic_evidence"]),
            "event_evidence": len(recall_pack["event_evidence"]),
            "conflicts": len(recall_pack["conflicts"]),
            "memory_links": int(
                (recall_pack.get("memory_link_context") or {}).get("link_count", 0)
                if isinstance(recall_pack.get("memory_link_context"), dict)
                else 0
            ),
        }
        if isinstance(evidence_pack, dict):
            summary["answer_evidence_items"] = len(evidence_pack.get("evidence_items", []))
        return summary

    def _empty_memory_link_context_trace(self) -> dict[str, Any]:
        # 結果
        return {
            "selected_memory_unit_count": 0,
            "link_count": 0,
            "label_counts": {},
            "representative_links": [],
            "result_status": "empty",
        }

    def _summarize_memory_link_context(self, value: Any) -> dict[str, Any]:
        # 形状
        if not isinstance(value, dict):
            return self._empty_memory_link_context_trace()

        # 代表 link
        representative_links: list[dict[str, Any]] = []
        for item in value.get("representative_links", []):
            if not isinstance(item, dict):
                continue
            representative_links.append(
                {
                    "memory_link_id": item.get("memory_link_id"),
                    "label": item.get("label"),
                    "selected_endpoint": item.get("selected_endpoint"),
                    "source_memory_unit_id": item.get("source_memory_unit_id"),
                    "target_memory_unit_id": item.get("target_memory_unit_id"),
                    "summary_text": item.get("summary_text"),
                }
            )
            if len(representative_links) >= 5:
                break

        # 結果
        return {
            "selected_memory_unit_count": int(value.get("selected_memory_unit_count", 0) or 0),
            "link_count": int(value.get("link_count", 0) or 0),
            "label_counts": value.get("label_counts", {}),
            "representative_links": representative_links,
            "result_status": value.get("result_status", "empty"),
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
        if self._observation_summary_is_desktop_vision_capture(observation_summary):
            return False
        if self._client_context_has_desktop_wake_observation(client_context):
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

    def _client_context_has_desktop_wake_observation(
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
            and isinstance(item.get("source_kind"), str)
            and item["source_kind"].strip() == "desktop"
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
