from __future__ import annotations

from typing import Any

from otomekairo.llm.contracts import validate_world_state_contract
from otomekairo.world_state.models import (
    WorldStatePendingIntent,
    WorldStateScheduleContext,
    WorldStateSourcePack,
    WorldStateVisualContext,
)


class LLMMockWorldStateMixin:
    def generate_world_state(
        self,
        role_definition: dict,
        source_pack: WorldStateSourcePack,
    ) -> dict[str, Any]:
        # model確認
        self._assert_mock_model(role_definition)

        # source pack
        trigger_kind = source_pack.trigger_kind
        source_candidates = {
            candidate.state_type: candidate
            for candidate in source_pack.state_sources
        }

        # 候補群
        state_candidates: list[dict[str, Any]] = []
        visual_summary = self._mock_world_state_visual_summary(
            visual_context=source_pack.visual_context,
        )
        visual_source = source_candidates.get("visual_context")
        if visual_summary is not None and visual_source is not None:
            state_candidates.append(
                {
                    "candidate_ref": visual_source.candidate_ref,
                    "summary_text": visual_summary,
                    "confidence_hint": "medium",
                    "salience_hint": "high",
                    "ttl_hint": "short",
                }
            )

        social_summary = self._mock_world_state_structured_summary(source_pack.social_context_context)
        social_source = source_candidates.get("social_context")
        if social_summary is not None and social_source is not None:
            state_candidates.append(
                {
                    "candidate_ref": social_source.candidate_ref,
                    "summary_text": social_summary,
                    "confidence_hint": "medium",
                    "salience_hint": "medium",
                    "ttl_hint": "short",
                }
            )

        schedule_summary = self._mock_world_state_schedule_summary(source_pack.schedule_context)
        schedule_source = source_candidates.get("schedule")
        if schedule_summary is not None and schedule_source is not None:
            state_candidates.append(
                {
                    "candidate_ref": schedule_source.candidate_ref,
                    "summary_text": schedule_summary,
                    "confidence_hint": "medium",
                    "salience_hint": "high" if trigger_kind in {"wake", "background_thinking"} else "medium",
                    "ttl_hint": "medium",
                }
            )

        for state_type, summary_text in (
            (
                "external_service",
                self._mock_world_state_structured_summary(source_pack.external_service_context),
            ),
            (
                "body",
                self._mock_world_state_structured_summary(source_pack.body_context),
            ),
            (
                "device",
                self._mock_world_state_structured_summary(source_pack.device_context),
            ),
        ):
            source_candidate = source_candidates.get(state_type)
            if summary_text is None or source_candidate is None:
                continue
            state_candidates.append(
                {
                    "candidate_ref": source_candidate.candidate_ref,
                    "summary_text": summary_text,
                    "confidence_hint": "medium",
                    "salience_hint": "medium",
                    "ttl_hint": "medium",
                }
            )

        for state_type, summary_text in (
            (
                "environment",
                self._mock_world_state_structured_summary(source_pack.environment_context),
            ),
            (
                "location",
                self._mock_world_state_structured_summary(source_pack.location_context),
            ),
        ):
            source_candidate = source_candidates.get(state_type)
            if summary_text is None or source_candidate is None:
                continue
            state_candidates.append(
                {
                    "candidate_ref": source_candidate.candidate_ref,
                    "summary_text": summary_text,
                    "confidence_hint": "medium",
                    "salience_hint": "medium",
                    "ttl_hint": "medium",
                }
            )

        # payload
        payload = {
            "state_candidates": state_candidates[:4],
        }
        validate_world_state_contract(payload, source_pack=source_pack)
        return payload

    def _mock_world_state_visual_summary(
        self,
        *,
        visual_context: WorldStateVisualContext | None,
    ) -> str | None:
        if isinstance(visual_context, WorldStateVisualContext):
            if isinstance(visual_context.summary_text, str) and visual_context.summary_text.strip():
                return visual_context.summary_text
            if (
                isinstance(visual_context.visual_summary_text, str)
                and visual_context.visual_summary_text.strip()
            ):
                return visual_context.visual_summary_text
        return None

    def _mock_world_state_structured_summary(self, context: Any) -> str | None:
        summary_text = getattr(context, "summary_text", None)
        if not isinstance(summary_text, str) or not summary_text.strip():
            return None
        return summary_text.strip()

    def _mock_world_state_schedule_summary(
        self,
        schedule_context: WorldStateScheduleContext | None,
    ) -> str | None:
        if not isinstance(schedule_context, WorldStateScheduleContext):
            return None
        if isinstance(schedule_context.summary_text, str) and schedule_context.summary_text.strip():
            return schedule_context.summary_text.strip()
        if not isinstance(schedule_context.pending_intent, WorldStatePendingIntent):
            return None
        if (
            isinstance(schedule_context.pending_intent.intent_summary, str)
            and schedule_context.pending_intent.intent_summary.strip()
        ):
            return f"近いうちに {schedule_context.pending_intent.intent_summary.strip()} を見直す予定が前景にある。"
        if (
            isinstance(schedule_context.pending_intent.reason_summary, str)
            and schedule_context.pending_intent.reason_summary.strip()
        ):
            return f"近い予定として {schedule_context.pending_intent.reason_summary.strip()}"
        return None
