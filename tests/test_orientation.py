from __future__ import annotations

import unittest

from otomekairo.interaction import InteractionContext, ParticipantContext
from otomekairo.llm.contexts import CurrentInput
from otomekairo.llm.prompts import _build_decision_trigger_policy
from otomekairo.service.input.capability_context import ServiceInputCapabilityContextMixin
from otomekairo.service.input.pipeline import ServiceInputPipelineMixin
from otomekairo.service.input.world_state import ServiceInputWorldStateMixin
from otomekairo.world_state.models import WorldStateExternalServiceContext


class _PipelineSubject(ServiceInputPipelineMixin):
    pass


class _CapabilityContextSubject(ServiceInputCapabilityContextMixin):
    def _client_context_text(self, value: object, *, limit: int) -> str | None:
        _ = limit
        if not isinstance(value, str):
            return None
        stripped = value.strip()
        return stripped or None

    def _compact_capability_request_summary(self, value: object) -> dict | None:
        if not isinstance(value, dict):
            return None
        return value

    def _compact_capability_followup_observation_summary(self, value: object) -> dict | None:
        if not isinstance(value, dict):
            return None
        return value

    def _observed_persons_from_mcp_observation(self, value: object) -> list:
        _ = value
        return []


class _WorldStateSubject(ServiceInputWorldStateMixin):
    def _parse_iso(self, value: str):
        raise NotImplementedError

    def _client_context_text(self, value: object, *, limit: int) -> str | None:
        _ = limit
        if not isinstance(value, str):
            return None
        stripped = value.strip()
        return stripped or None


def _interaction() -> InteractionContext:
    return InteractionContext(
        interaction_ref="interaction:web:direct:web:abc",
        speaker_ref="person:web:abc",
        participants=(
            ParticipantContext(person_ref="person:web:abc", display_name="マスター"),
        ),
    )


class OrientationTests(unittest.TestCase):
    def test_person_origin_capability_result_keeps_person_current_input(self) -> None:
        interaction = _interaction()
        current = _PipelineSubject()._build_current_input(
            input_text='capability result を受信。 items: []',
            trigger_kind="capability_result",
            interaction_context=interaction,
            capability_request_summary={
                "source_current_input": {
                    "sender_kind": "person",
                    "sender_ref": "person:web:abc",
                    "source_kind": "user_message",
                    "response_target_refs": ["person:web:abc"],
                    "text": "返信してみたら？",
                    "interaction_context": interaction.to_prompt_payload(),
                }
            },
        )

        self.assertEqual(current.sender_kind, "person")
        self.assertEqual(current.source_kind, "user_message")
        self.assertEqual(current.text, "返信してみたら？")
        self.assertEqual(current.response_target_refs, ("person:web:abc",))

    def test_wake_origin_capability_result_stays_arrival(self) -> None:
        current = _PipelineSubject()._build_current_input(
            input_text="capability result を受信。",
            trigger_kind="capability_result",
            interaction_context=None,
            capability_request_summary={
                "source_current_input": {
                    "sender_kind": "system",
                    "sender_ref": None,
                    "source_kind": "wake",
                    "response_target_refs": [],
                    "text": "定期思考。",
                }
            },
        )

        self.assertEqual(current.sender_kind, "capability")
        self.assertEqual(current.source_kind, "capability_result")
        self.assertEqual(current.text, "capability result を受信。")
        self.assertEqual(current.response_target_refs, ())

    def test_from_source_payload_rejects_non_person(self) -> None:
        self.assertIsNone(
            CurrentInput.from_source_payload(
                {
                    "sender_kind": "capability",
                    "source_kind": "capability_result",
                    "text": "結果",
                    "response_target_refs": ["person:web:abc"],
                    "sender_ref": "person:web:abc",
                    "interaction_context": _interaction().to_prompt_payload(),
                }
            )
        )

    def test_mcp_tool_results_use_separate_world_state_keys(self) -> None:
        service = _WorldStateSubject()
        notifications = WorldStateExternalServiceContext(
            summary_text="通知は新着なし",
            capability_id="mcp.call_tool",
            mcp_server_id="elyth",
            tool_name="get_notifications",
            service="elyth/get_notifications",
        )
        thread = WorldStateExternalServiceContext(
            summary_text="儀式スレッドにリプライが5件ある。",
            capability_id="mcp.call_tool",
            mcp_server_id="elyth",
            tool_name="get_thread",
            service="elyth/get_thread",
        )

        self.assertEqual(
            service._world_state_integration_policy(
                state_type="external_service",
                scope_type="world",
                scope_key="world",
                context=notifications,
            )["key"],
            "external_service:elyth:get_notifications",
        )
        self.assertEqual(
            service._world_state_integration_policy(
                state_type="external_service",
                scope_type="world",
                scope_key="world",
                context=thread,
            )["key"],
            "external_service:elyth:get_thread",
        )

    def test_person_orientation_followup_keeps_conversation_policy(self) -> None:
        interaction = _interaction()
        origin = CurrentInput(
            sender_kind="person",
            sender_ref="person:web:abc",
            source_kind="user_message",
            response_target_refs=("person:web:abc",),
            interaction_context=interaction,
            text="返信してみたら？",
        )
        context = _CapabilityContextSubject()._build_capability_result_decision_context(
            trigger_kind="capability_result",
            observation_summary={
                "capability_id": "mcp.call_tool",
                "mcp_server_id": "elyth",
                "tool_name": "get_notifications",
                "status": "completed",
                "mcp_result_summary": '{"items":[]}',
            },
            capability_request_summary={
                "capability_id": "mcp.call_tool",
                "source_current_input": origin.to_prompt_payload(),
            },
            work_log=[{"capability_id": "mcp.call_tool", "tool_name": "get_notifications"}],
            current_input=origin,
        )

        self.assertIsNotNone(context)
        assert context is not None
        self.assertEqual(context["orientation_kind"], "person")
        self.assertIn("mcp.call_tool", context["allowed_followup_capability_ids"])
        self.assertIn("向きは起点の人物発話", context["followup_policy_summary"])
        self.assertIn("今回の結果で向きが果たされていれば", context["followup_policy_summary"])
        self.assertEqual(
            context["work_log"],
            [{"capability_id": "mcp.call_tool", "tool_name": "get_notifications"}],
        )

        policies = _build_decision_trigger_policy(
            initiative_context=None,
            capability_result_context=context,
        )
        self.assertTrue(any("起点の人物発話" in item for item in policies))
        self.assertTrue(any("向きが果たされていれば" in item for item in policies))
        self.assertFalse(any("会話を打ち切らない" in item for item in policies))
        self.assertFalse(any("speech / noop / pending_intent で閉じ" in item for item in policies))

    def test_completed_mcp_tool_is_excluded_from_immediate_followup(self) -> None:
        interaction = _interaction()
        origin = CurrentInput(
            sender_kind="person",
            sender_ref="person:web:abc",
            source_kind="user_message",
            response_target_refs=("person:web:abc",),
            interaction_context=interaction,
            text="なんか投稿してみて",
        )
        context = _CapabilityContextSubject()._build_capability_result_decision_context(
            trigger_kind="capability_result",
            observation_summary={
                "capability_id": "mcp.call_tool",
                "mcp_server_id": "elyth",
                "tool_name": "create_post",
                "status": "completed",
                "is_error": False,
                "mcp_result_summary": '{"結果":"投稿を作成しました"}',
            },
            capability_request_summary={
                "capability_id": "mcp.call_tool",
                "source_current_input": origin.to_prompt_payload(),
            },
            work_log=[
                {
                    "capability_id": "mcp.call_tool",
                    "mcp_server_id": "elyth",
                    "tool_name": "create_post",
                    "status": "completed",
                }
            ],
            current_input=origin,
        )

        self.assertIsNotNone(context)
        assert context is not None
        self.assertIn(
            {
                "capability_id": "mcp.call_tool",
                "constraint": "exclude_completed_mcp_tool",
                "mcp_server_id": "elyth",
                "tool_name": "create_post",
            },
            context["followup_constraints"],
        )
        self.assertIn("今回完了した tool は elyth/create_post", context["followup_policy_summary"])

        policies = _build_decision_trigger_policy(
            initiative_context=None,
            capability_result_context=context,
        )
        self.assertTrue(any("elyth/create_post" in item for item in policies))
        self.assertTrue(any("未完了の別手順か発話" in item for item in policies))

    def test_failed_mcp_tool_is_not_excluded_from_followup(self) -> None:
        interaction = _interaction()
        origin = CurrentInput(
            sender_kind="person",
            sender_ref="person:web:abc",
            source_kind="user_message",
            response_target_refs=("person:web:abc",),
            interaction_context=interaction,
            text="なんか投稿してみて",
        )
        context = _CapabilityContextSubject()._build_capability_result_decision_context(
            trigger_kind="capability_result",
            observation_summary={
                "capability_id": "mcp.call_tool",
                "mcp_server_id": "elyth",
                "tool_name": "create_post",
                "status": "failed",
                "is_error": True,
                "error": "mcp_tool_error",
            },
            capability_request_summary={
                "capability_id": "mcp.call_tool",
                "source_current_input": origin.to_prompt_payload(),
            },
            current_input=origin,
        )

        self.assertIsNotNone(context)
        assert context is not None
        self.assertNotIn("followup_constraints", context)
        self.assertNotIn("今回完了した tool", context["followup_policy_summary"])


if __name__ == "__main__":
    unittest.main()
