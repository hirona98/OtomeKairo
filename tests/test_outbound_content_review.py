import unittest
from copy import deepcopy
from pathlib import Path
import tempfile
from unittest.mock import Mock

from otomekairo.defaults import build_default_state
from otomekairo.interaction import InteractionContext, ParticipantContext
from otomekairo.llm.contracts import LLMError, validate_outbound_content_review_contract
from otomekairo.service.capability import (
    OutboundContentReviewFailureError,
    OutboundContentReviewWithheldError,
    ServiceCapabilityMixin,
)
from otomekairo.service.input.pipeline import (
    OUTBOUND_CONTENT_REVIEW_RETRY_FEEDBACK,
    ServiceInputPipelineMixin,
)
from otomekairo.service.input.trace_build import ServiceInputTraceBuildMixin
from otomekairo.service.app import OtomeKairoService


class _Store:
    def __init__(self) -> None:
        self.state = build_default_state()
        self.state["mcp_servers"]["e-stat"]["enabled"] = True

    def read_state(self) -> dict:
        return deepcopy(self.state)

    def get_current_activity_state(self, **kwargs) -> None:
        return None


class _Reviewer:
    def __init__(self, outcome: str = "allow") -> None:
        self.outcome = outcome
        self.calls: list[dict] = []

    def generate_outbound_content_review(self, *, model_config: dict, review_context: dict) -> dict:
        self.calls.append(deepcopy(review_context))
        return {
            "outcome": self.outcome,
            "reason_summary": "候補本文を含まない判定理由。",
        }


class _Service(ServiceCapabilityMixin):
    def __init__(self, reviewer: _Reviewer) -> None:
        self.store = _Store()
        self.llm = reviewer


class _PipelineService(ServiceInputPipelineMixin):
    def __init__(self) -> None:
        self.store = _Store()
        self._debug_cycle_label = Mock(return_value="cycle:test")
        self._short_identifier = Mock(return_value="memory:test")
        self._pipeline_assistant_message_target_client_id = Mock(return_value=None)
        self._pipeline_augmented_query_text = Mock(return_value="投稿して")
        self._build_visual_observation_decision_context = Mock(return_value=None)
        self._summarize_activity_context = Mock(return_value=None)
        self._build_selected_persona_context = Mock(return_value=object())
        self._persona_context_trace_summary = Mock(return_value={})
        self._build_pipeline_recall_inputs = Mock(
            return_value={
                "recall_hint": {},
                "recall_pack": {},
                "answer_contract": {},
                "evidence_pack": {},
            }
        )
        self._build_pipeline_internal_contexts = Mock(
            return_value={
                "time_context": {},
                "affect_context": {},
                "drive_state_summary": None,
                "foreground_world_state": None,
                "activity_context": None,
                "activity_trace": None,
                "ongoing_action_summary": None,
                "autonomous_run_summaries": None,
                "capability_decision_view": None,
                "initiative_context": None,
                "capability_result_context": None,
                "self_state_context": None,
                "people_context": [],
                "relationship_context": None,
                "prediction_error_context": None,
                "default_mode_context": None,
                "workspace_context": None,
                "world_state_trace": None,
            }
        )


class _TraceService(ServiceInputTraceBuildMixin):
    pass


class _RecordingWebSocket:
    def __init__(self) -> None:
        self.payloads: list[dict] = []

    def send_json(self, payload: dict) -> None:
        self.payloads.append(deepcopy(payload))

    def close(self) -> None:
        return None


def _tool() -> dict:
    return {
        "name": "create_post",
        "description": "投稿する",
        "inputSchema": {
            "type": "object",
            "properties": {"content": {"type": "string"}},
        },
    }


def _input(content: str) -> dict:
    return {
        "mcp_server_id": "e-stat",
        "tool_name": "create_post",
        "arguments": {"content": content},
    }


class OutboundContentReviewTests(unittest.TestCase):
    def test_contract_accepts_allow_and_withhold(self) -> None:
        for outcome in ("allow", "withhold"):
            validate_outbound_content_review_contract(
                {"outcome": outcome, "reason_summary": "判定理由"}
            )

    def test_contract_rejects_rewrite_and_extra_fields(self) -> None:
        with self.assertRaises(LLMError):
            validate_outbound_content_review_contract(
                {"outcome": "rewrite", "reason_summary": "判定理由"}
            )
        with self.assertRaises(LLMError):
            validate_outbound_content_review_contract(
                {"outcome": "allow", "reason_summary": "判定理由", "arguments": {}}
            )

    def test_allow_reviews_only_tool_metadata_and_final_arguments(self) -> None:
        reviewer = _Reviewer("allow")
        service = _Service(reviewer)

        audit = service._review_mcp_outbound_content(
            input_payload=_input("公開情報だけの投稿"),
            mcp_tool=_tool(),
            review_attempt=1,
        )

        self.assertEqual(audit["outcome"], "allow")
        self.assertEqual(audit["review_attempt"], 1)
        self.assertNotIn("arguments", audit)
        self.assertNotIn("reason_summary", audit)
        self.assertEqual(
            set(reviewer.calls[0]),
            {"channel", "tool", "arguments"},
        )
        self.assertEqual(reviewer.calls[0]["arguments"], {"content": "公開情報だけの投稿"})

    def test_llm_withhold_raises_without_persisting_reason_or_arguments(self) -> None:
        reviewer = _Reviewer("withhold")
        service = _Service(reviewer)

        with self.assertRaises(OutboundContentReviewWithheldError) as raised:
            service._review_mcp_outbound_content(
                input_payload=_input("審査対象の本文"),
                mcp_tool=_tool(),
                review_attempt=2,
            )

        audit = raised.exception.audit_summary
        self.assertEqual(audit["reason_code"], "reviewer_withheld")
        self.assertEqual(audit["review_attempt"], 2)
        self.assertNotIn("arguments", audit)
        self.assertNotIn("reason_summary", audit)

    def test_known_configured_secret_is_withheld_before_llm(self) -> None:
        reviewer = _Reviewer("allow")
        service = _Service(reviewer)
        service.store.state["mcp_servers"]["e-stat"]["env"]["E_STAT_APP_ID"] = "secret-value"

        with self.assertRaises(OutboundContentReviewWithheldError) as raised:
            service._review_mcp_outbound_content(
                input_payload=_input("prefix secret-value suffix"),
                mcp_tool=_tool(),
                review_attempt=1,
            )

        self.assertEqual(raised.exception.audit_summary["reason_code"], "known_secret_detected")
        self.assertEqual(reviewer.calls, [])

    def test_disabled_policy_bypasses_review(self) -> None:
        reviewer = _Reviewer("withhold")
        service = _Service(reviewer)
        service.store.state["mcp_servers"]["e-stat"]["outbound_content_review_required"] = False

        audit = service._review_mcp_outbound_content(
            input_payload=_input("任意の本文"),
            mcp_tool=_tool(),
            review_attempt=1,
        )

        self.assertIsNone(audit)
        self.assertEqual(reviewer.calls, [])

    def test_invalid_test_double_outcome_fails_closed(self) -> None:
        reviewer = _Reviewer("rewrite")
        service = _Service(reviewer)

        with self.assertRaises(OutboundContentReviewFailureError) as raised:
            service._review_mcp_outbound_content(
                input_payload=_input("本文"),
                mcp_tool=_tool(),
                review_attempt=1,
            )

        self.assertEqual(raised.exception.audit_summary["result_status"], "internal_failure")

    def test_pipeline_regenerates_once_then_returns_noop_and_system_notice(self) -> None:
        service = _PipelineService()
        initial_decision = {
            "kind": "capability_request",
            "reason_code": "post",
            "reason_summary": "投稿する",
            "capability_request": {},
        }
        retry_decision = {
            "kind": "noop",
            "reason_code": "safe_noop",
            "reason_summary": "送らない",
            "capability_request": None,
        }
        service._run_pipeline_decision = Mock(side_effect=[initial_decision, retry_decision])
        first_audit = {
            "mcp_server_id": "e-stat",
            "tool_name": "create_post",
            "result_status": "withheld",
            "outcome": "withhold",
            "reason_code": "reviewer_withheld",
            "review_attempt": 1,
        }
        service._run_pipeline_output = Mock(
            side_effect=OutboundContentReviewWithheldError(audit_summary=first_audit)
        )
        interaction = InteractionContext(
            interaction_ref="interaction:test",
            speaker_ref="person:test",
            participants=(ParticipantContext("person:test", "テスト"),),
        )

        result = service._run_input_pipeline(
            state=service.store.read_state(),
            started_at="2026-08-11T12:00:00+09:00",
            input_text="投稿して",
            recent_turns=[],
            cycle_id="cycle:test",
            interaction_context=interaction,
        )

        self.assertEqual(result["decision"]["kind"], "noop")
        self.assertEqual(
            result["decision"]["outbound_content_review"]["attempts"],
            [first_audit],
        )
        self.assertEqual(result["system_notice"]["code"], "outbound_content_review_withheld")
        self.assertTrue(result["system_notice"]["conversation_visible"])
        self.assertEqual(service._run_pipeline_decision.call_count, 2)
        self.assertEqual(
            service._run_pipeline_decision.call_args.kwargs["outbound_content_review_feedback"],
            OUTBOUND_CONTENT_REVIEW_RETRY_FEEDBACK,
        )
        self.assertEqual(service._run_pipeline_output.call_count, 1)

    def test_system_notice_is_persisted_as_system_conversation_row(self) -> None:
        interaction = InteractionContext(
            interaction_ref="interaction:test",
            speaker_ref="person:test",
            participants=(ParticipantContext("person:test", "テスト"),),
        )
        events = _TraceService()._build_cycle_events(
            cycle_id="cycle:test",
            memory_set_id="memory_set:test",
            input_event_kind="conversation_input",
            input_event_role="person",
            interaction_context=interaction,
            input_text="投稿して",
            started_at="2026-08-11T12:00:00+09:00",
            finished_at="2026-08-11T12:00:01+09:00",
            decision={
                "kind": "noop",
                "reason_code": "outbound_content_review_withheld",
                "reason_summary": "送信しない",
            },
            result_kind="noop",
            system_notice={
                "code": "outbound_content_review_withheld",
                "message": "外部送信を見送りました。",
                "conversation_visible": True,
            },
        )

        notice_event = next(event for event in events if event["kind"] == "system_notice")
        self.assertEqual(notice_event["role"], "system")
        self.assertEqual(notice_event["text"], "外部送信を見送りました。")
        self.assertNotIn("arguments", str(notice_event))

    def test_withheld_request_never_reaches_connected_mcp_connector(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            try:
                state = service.store.read_state()
                state["mcp_servers"]["e-stat"]["enabled"] = True
                service.store.write_state(state)
                service.llm = _Reviewer("withhold")
                websocket = _RecordingWebSocket()
                session_id = service.register_event_stream_connection(websocket)
                service.handle_event_stream_message(
                    session_id,
                    {
                        "type": "hello",
                        "client_id": "mcp-client-connector-main",
                        "client_kind": "capability_connector",
                        "caps": [{"id": "mcp.call_tool", "version": "1"}],
                        "mcp_servers": [
                            {
                                "mcp_server_id": "e-stat",
                                "transport": "stdio",
                                "tools": [_tool()],
                            }
                        ],
                    },
                )

                with self.assertRaises(OutboundContentReviewWithheldError):
                    service._dispatch_capability_request(
                        memory_set_id=state["selected_memory_set_id"],
                        capability_id="mcp.call_tool",
                        input_payload=_input("審査で保留する投稿"),
                        current_time="2026-08-11T12:00:00+09:00",
                        goal_summary="投稿する",
                        wait_for_response=False,
                        component="Test",
                    )

                self.assertEqual(websocket.payloads, [])
                self.assertEqual(service._pending_capability_requests, {})
                self.assertIsNone(
                    service.store.get_ongoing_action(
                        memory_set_id=state["selected_memory_set_id"],
                        current_time="2026-08-11T12:00:00+09:00",
                    )
                )
            finally:
                service.close_tts_runtime()


if __name__ == "__main__":
    unittest.main()
