from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from otomekairo.http_server import OtomeKairoHandler
from otomekairo.llm.client import LLMError
from otomekairo.service.app import OtomeKairoService
from otomekairo.service.common import ServiceError
from otomekairo.service.input.mixin import ServiceInputMixin
from otomekairo.service.input.pipeline import ServiceInputPipelineMixin
from otomekairo.service.input.wake_pipeline import ServiceInputWakePipelineMixin
from otomekairo.service.spontaneous.mixin import ServiceSpontaneousMixin
from otomekairo.service.spontaneous.wake import ServiceSpontaneousWakeMixin


class _WakeApi(ServiceSpontaneousMixin):
    def __init__(self):
        self._execute_wake_cycle = Mock(return_value={"result_kind": "noop"})

    def _require_token(self, token):
        if token != "test-token":
            raise ServiceError(401, "invalid_token", "Invalid token.")
        return {}


class _WakePipeline(ServiceInputWakePipelineMixin, ServiceSpontaneousWakeMixin):
    def __init__(self):
        self._run_input_pipeline = Mock(return_value={"decision": {"kind": "noop"}})
        self._run_autonomous_initiative_entry_check = Mock(side_effect=AssertionError("entry gate"))
        self._run_wake_policy_observations = Mock(side_effect=AssertionError("scheduled observations"))

    def _debug_cycle_label(self, cycle_id):
        return cycle_id


@pytest.mark.parametrize("message", [None, "", " \t\n", 1, False, [], {}])
def test_wake_rejects_invalid_message_before_running_cycle(message):
    service = _WakeApi()
    with pytest.raises(ServiceError) as error:
        service.trigger_wake("test-token", {"message": message})
    assert error.value.error_code == "invalid_wake_message"
    service._execute_wake_cycle.assert_not_called()


def test_wake_requires_authentication_before_accepting_message():
    service = _WakeApi()
    with pytest.raises(ServiceError) as error:
        service.trigger_wake(None, {"message": "完了したと伝えてほしい。"})
    assert error.value.error_code == "invalid_token"
    service._execute_wake_cycle.assert_not_called()


def test_http_wake_preserves_source_message_and_reference():
    service = _WakeApi()
    message = '  修正 "A" が完了。\n確認の返答を待っていると伝えてほしい。\n'
    payload = {
        "message": message,
        "client_context": {"source": "Codex"},
        "reference": {"uri": "/tmp/test-reference.txt", "content_hint": "text"},
    }
    handler = object.__new__(OtomeKairoHandler)
    handler.path = "/api/wake"
    handler.headers = {"Authorization": "Bearer test-token"}
    handler.server = SimpleNamespace(service=service)
    handler._read_json_body = Mock(return_value=payload)
    handler._write_success = Mock()

    handler._dispatch("POST")

    call = service._execute_wake_cycle.call_args.kwargs
    assert call["wake_message"] == message
    assert call["client_context"] == {"source": "Codex"}
    assert call["reference_payload"] == payload["reference"]
    assert call["interaction_context"] is None
    assert call["trigger_kind"] == "wake"
    handler._write_success.assert_called_once_with(200, {"result_kind": "noop"})


def test_message_enters_judgment_without_pending_or_initiative_gate():
    service = _WakePipeline()
    message = "確認を待っていると伝えてほしい。\n" + "段落を保った依頼本文。\n" * 600
    reference = {"text": "参照資料の全文。"}

    pipeline, input_text, _ = service._run_wake_pipeline(
        state={},
        started_at="2026-10-24T15:00:00+09:00",
        trigger_kind="wake",
        client_context={"source": "Codex"},
        interaction_context=None,
        recent_turns=[],
        selected_candidate=None,
        wake_message=message,
        cycle_id="cycle:test",
        reference_context=reference,
    )

    call = service._run_input_pipeline.call_args.kwargs
    assert input_text.endswith(message)
    assert "Codex" in input_text
    assert call["input_text"] == input_text
    assert call["reference_context"] is reference
    assert call["is_api_request"] is True
    assert pipeline["decision"]["kind"] == "noop"
    service._run_autonomous_initiative_entry_check.assert_not_called()
    service._run_wake_policy_observations.assert_not_called()
    current_input = ServiceInputPipelineMixin._build_current_input(
        service, input_text=input_text, trigger_kind="wake", interaction_context=None,
        is_api_request=call["is_api_request"],
    )
    assert current_input.sender_kind == "system"
    assert current_input.source_kind == "wake"
    assert current_input.response_target_refs == ()
    assert current_input.text.endswith(message)
    assert current_input.to_prompt_payload()["is_api_request"] is True


def test_wake_without_message_preserves_empty_wake_request():
    service = _WakeApi()
    service.trigger_wake("test-token", {})
    call = service._execute_wake_cycle.call_args.kwargs
    assert call["wake_message"] is None
    assert call["reference_payload"] is None
    assert call["client_context"] == {}


@pytest.mark.parametrize("kind", ["speech", "capability_request", "autonomous_run", "pending_intent", "noop"])
def test_api_request_keeps_full_decision_with_self_activity_candidates(kind):
    service = ServiceInputMixin()
    decision = {"kind": kind, "reason_summary": "依頼固有の実行条件に基づく判断。"}
    service.llm = SimpleNamespace(generate_decision=Mock(return_value=decision))
    current_input = service._build_current_input(
        input_text="API経由の依頼。入力源は Codex。\n依頼本文:\n終了したと伝えてほしい。",
        trigger_kind="wake", interaction_context=None, is_api_request=True,
    )
    capabilities = [{"id": "vision.capture", "available": True}]
    reference = {"text": "依頼と一緒に届いた参照全文。"}

    result = service._run_pipeline_decision(
        input_text=current_input.text, current_input=current_input, trigger_kind="wake",
        recent_turns=[], time_context={}, affect_context={}, drive_state_summary=None,
        foreground_world_state=None, activity_context=None, ongoing_action_summary=None,
        autonomous_run_summaries=None, capability_decision_view=capabilities,
        agent_skill_context=None, initiative_context=None, capability_result_context=None,
        current_visual_observations=[], self_state_context=None, people_context=[],
        relationship_context=None, prediction_error_context=None, default_mode_context=None,
        workspace_context={"workspace_candidates": [
            {"factor_ref": "periodic_thought_topic:test", "kind": "periodic_thought_topic"},
        ]},
        workspace_candidate_pool=[
            {"factor_ref": "periodic_thought_topic:test", "kind": "periodic_thought_topic"},
        ],
        recall_hint={}, recall_pack={}, reference_context=reference,
        model_config={}, persona_context=None, cycle_label="cycle:test",
    )

    service.llm.generate_decision.assert_called_once()
    context = service.llm.generate_decision.call_args.kwargs["context"]
    assert context.comparison_scope == "full"
    assert context.current_input is current_input
    assert context.input_text == current_input.text
    assert context.capability_decision_view is capabilities
    assert context.reference_context is reference
    assert result is decision


def test_api_request_keeps_original_input_for_skill_and_recall_materials():
    service = ServiceInputMixin()
    current_input = service._build_current_input(
        input_text="Codex からの依頼全文。", trigger_kind="wake",
        interaction_context=None, is_api_request=True,
    )
    service._build_pipeline_recall_inputs = Mock(side_effect=AssertionError("isolated recall"))
    service._build_agent_skill_context = Mock(return_value={"activation": None})
    service._orientation_work_log = Mock(return_value=[])
    result = service._build_pipeline_skill_and_self_activity_materials(
        state={}, cycle_id="cycle:test", started_at="2026-10-24T15:00:00+09:00",
        current_input=current_input, recent_turns=[], trigger_kind="wake",
        observation_summary=None, capability_request_summary=None,
        due_periodic_thought_topics=[], ongoing_action_summary=None,
        capability_decision_view=[], initiative_context=None,
        workspace_context={"workspace_candidates": [
            {"factor_ref": "periodic_thought_topic:test", "kind": "periodic_thought_topic"},
        ]},
        model_config={}, cycle_label="cycle:test",
    )
    service._build_pipeline_recall_inputs.assert_not_called()
    assert service._build_agent_skill_context.call_args.kwargs["current_input"] is current_input
    assert result["self_activity_current_input"] is None


@pytest.mark.parametrize("failure", [False, True])
def test_wake_request_trace_preserves_request_marker_and_full_text(tmp_path, failure):
    service = OtomeKairoService(tmp_path)
    state = service.store.read_state()
    state["console_access_token"] = "test-token"
    for preset in state["model_presets"].values():
        preset["model"] = "mock"
    state["memory_sets"][state["selected_memory_set_id"]]["embedding"]["model"] = "mock"
    service.store.write_state(state)
    message = "OtomeKairo の修正が終了したと伝えてほしい。\n" + "依頼の段落。\n" * 60
    interpretation = service.llm.generate_input_interpretation
    interpretation_calls = []

    def interpret(**kwargs):
        interpretation_calls.append(kwargs["current_input"])
        if failure:
            raise LLMError("test interpretation failure")
        return interpretation(**kwargs)

    with patch.object(type(service.llm), "generate_input_interpretation", side_effect=interpret):
        result = service.trigger_wake("test-token", {
            "message": message, "client_context": {"source": "Codex"},
        })

    assert result["result_kind"] == ("internal_failure" if failure else "noop")
    assert len(interpretation_calls) == 1
    trace = service.store.get_cycle_trace(result["cycle_id"])
    for recorded_input in [
        trace["input_trace"]["current_input"],
        trace["result_trace"]["trigger_compact_summary"]["current_input_summary"],
    ]:
        assert recorded_input == interpretation_calls[0].to_prompt_payload()
        assert recorded_input["is_api_request"] is True
        assert recorded_input["text"].endswith(message)
        assert "Codex" in recorded_input["text"]


def test_client_context_cannot_mark_message_less_wake_as_request():
    service = _WakeApi()
    service.trigger_wake("test-token", {"client_context": {"is_api_request": True}})
    assert service._execute_wake_cycle.call_args.kwargs["wake_message"] is None


def test_request_marker_is_only_valid_for_wake():
    with pytest.raises(ValueError):
        ServiceInputPipelineMixin()._build_current_input(
            input_text="定期思考。", trigger_kind="background_thinking",
            interaction_context=None, is_api_request=True,
        )
