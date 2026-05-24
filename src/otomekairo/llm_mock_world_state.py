from __future__ import annotations

from typing import Any

from otomekairo.llm_contracts import validate_world_state_contract
from otomekairo.world_state_models import (
    WorldStateClientContext,
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
        client_context = source_pack.client_context
        current_input_summary = source_pack.current_input_summary.strip()

        # 候補群
        state_candidates: list[dict[str, Any]] = []
        visual_summary = self._mock_world_state_visual_summary(
            visual_context=source_pack.visual_context,
        )
        if visual_summary is not None:
            state_candidates.append(
                {
                    "state_type": "visual_context",
                    "scope": "topic:current_work",
                    "summary_text": visual_summary,
                    "confidence_hint": "medium",
                    "salience_hint": "high",
                    "ttl_hint": "short",
                }
            )

        social_summary = self._mock_world_state_structured_summary(source_pack.social_context_context)
        if social_summary is None:
            social_summary = self._mock_world_state_social_summary(client_context)
        if social_summary is not None:
            state_candidates.append(
                {
                    "state_type": "social_context",
                    "scope": "relationship:self|user",
                    "summary_text": social_summary,
                    "confidence_hint": "medium",
                    "salience_hint": "medium",
                    "ttl_hint": "short",
                }
            )

        user_state_summary = self._mock_world_state_user_summary(current_input_summary)
        if user_state_summary is not None:
            state_candidates.append(
                {
                    "state_type": "social_context",
                    "scope": "user",
                    "summary_text": user_state_summary,
                    "confidence_hint": "medium",
                    "salience_hint": "medium",
                    "ttl_hint": "medium",
                }
            )

        schedule_summary = self._mock_world_state_schedule_summary(source_pack.schedule_context)
        if schedule_summary is not None:
            state_candidates.append(
                {
                    "state_type": "schedule",
                    "scope": "self",
                    "summary_text": schedule_summary,
                    "confidence_hint": "medium",
                    "salience_hint": "high" if trigger_kind in {"wake", "background_wake"} else "medium",
                    "ttl_hint": "medium",
                }
            )

        for state_type, scope, summary_text in (
            (
                "external_service",
                "world",
                self._mock_world_state_structured_summary(source_pack.external_service_context),
            ),
            (
                "body",
                "self",
                self._mock_world_state_structured_summary(source_pack.body_context),
            ),
            (
                "device",
                "world",
                self._mock_world_state_structured_summary(source_pack.device_context),
            ),
        ):
            if summary_text is None:
                continue
            state_candidates.append(
                {
                    "state_type": state_type,
                    "scope": scope,
                    "summary_text": summary_text,
                    "confidence_hint": "medium",
                    "salience_hint": "medium",
                    "ttl_hint": "medium",
                }
            )

        for state_type, scope, summary_text in (
            (
                "environment",
                "world",
                self._mock_world_state_structured_summary(source_pack.environment_context),
            ),
            (
                "location",
                "world",
                self._mock_world_state_structured_summary(source_pack.location_context),
            ),
        ):
            if summary_text is None:
                continue
            state_candidates.append(
                {
                    "state_type": state_type,
                    "scope": scope,
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
        validate_world_state_contract(payload)
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

    def _mock_world_state_social_summary(self, client_context: WorldStateClientContext) -> str | None:
        active_app = getattr(client_context, "active_app", None)
        if not isinstance(active_app, str) or not active_app.strip():
            return None
        lowered = active_app.strip().lower()
        if any(token in lowered for token in ("slack", "discord", "teams", "zoom", "meet")):
            return f"{active_app.strip()} 上のやり取りが近い判断文脈として前景にある。"
        return None

    def _mock_world_state_user_summary(self, current_input_summary: str) -> str | None:
        if any(token in current_input_summary for token in ("会議中", "打ち合わせ", "ミーティング")):
            return "ユーザーは会議や打ち合わせの文脈にいる。"
        if any(token in current_input_summary for token in ("移動中", "電車", "外出")):
            return "ユーザーは移動や外出の途中にいる。"
        if any(token in current_input_summary for token in ("眠い", "寝る", "疲れた")):
            return "ユーザーは休息が必要そうな状態にある。"
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
