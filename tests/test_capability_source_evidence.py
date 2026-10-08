import json
from unittest.mock import Mock

from otomekairo.llm.prompts import build_speech_messages, build_speech_grounding_review_messages
from otomekairo.llm.contexts import SpeechContext
from otomekairo.memory.utils import localize_timestamp_fields
from otomekairo.service.capability import ServiceCapabilityMixin
from otomekairo.service.input.capability_context import ServiceInputCapabilityContextMixin
from otomekairo.service.input.trace_compact import ServiceInputTraceCompactMixin
from test_decision_contract import _current_input, _persona_context


class Service(ServiceCapabilityMixin, ServiceInputCapabilityContextMixin, ServiceInputTraceCompactMixin):
    def _clamp(self, value, *, limit):
        return value

    def _client_context_text(self, value, *, limit):
        return value.strip() if isinstance(value, str) and value.strip() else None


def test_original_visual_evidence_survives_request_and_result_context():
    service = Service()
    service._capability_ongoing_action_expires_at = Mock(return_value="2026-10-04T09:00:30+09:00")
    evidence = [{"vision_source_id": "vision_source:camera", "request_id": "vision_capture_request:test",
                 "observed_at": "2026-10-04T09:00:00+09:00", "summary_text": "詳細な観測。" * 120}]
    record = service._build_capability_request_record(
        memory_set_id="memory_set:test", capability_id="mcp.call_tool", target_client_id="client:test",
        input_payload={"mcp_server_id": "elyth", "tool_name": "create_post", "arguments": {"text": "投稿。"}},
        timeout_ms=30000, current_time="2026-10-04T09:00:00+09:00", manifest={},
        action_seed=None, wait_for_response=False, source_visual_observations=evidence,
    )
    # 元の処理側の変更が、結果待ちの根拠を変えない。
    evidence[0]["summary_text"] = "変更。"
    summary = service._capability_request_summary(record)
    context = service._build_capability_result_decision_context(
        trigger_kind="capability_result", observation_summary={"capability_id": "mcp.call_tool"},
        capability_request_summary=summary, current_input=_current_input(),
    )
    assert context["source_visual_observations"][0]["summary_text"] == "詳細な観測。" * 120
    assert context["source_visual_observations"][0]["observed_at"] == "2026-10-04T09:00:00+09:00"
    assert "source_visual_observations" not in record["input"]


def test_speech_and_grounding_receive_the_complete_capability_evidence():
    result_context = {"source_visual_observations": [{
        "observed_at": "2026-10-04T09:00:00+09:00", "summary_text": "詳細な観測。" * 120,
    }]}
    context = SpeechContext(
        input_text="結果を報告して。", current_input=_current_input(), recent_turns=[],
        time_context={}, affect_context={}, drive_state_summary=None, foreground_world_state=None,
        activity_context=None, ongoing_action_summary=None, initiative_context=None,
        visual_observation_context=None, self_state_context=None, relationship_context=None,
        prediction_error_context=None, workspace_context=None, recall_hint={}, recall_pack={},
        decision={"kind": "speech"}, capability_result_context=result_context,
    )
    messages = build_speech_messages(persona_context=_persona_context(), context=context)
    payloads = [json.loads(message["content"].split("\n")[1])
                for message in messages if message["role"] == "user" and message["content"].startswith("<<<OTOMEKAIRO_INTERNAL_CONTEXT>>>")]
    assert payloads[0]["internal_context"]["capability_result_context"] == localize_timestamp_fields(result_context)
    review = build_speech_grounding_review_messages(
        persona_context=_persona_context(), context=context, candidate_speech="結果を報告します。",
    )
    payload = json.loads(review[-1]["content"].split("\n")[1])
    assert payload["capability_result_context"] == localize_timestamp_fields(result_context)
