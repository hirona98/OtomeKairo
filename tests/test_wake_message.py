from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from otomekairo.http_server import OtomeKairoHandler
from otomekairo.service.common import ServiceError
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
    assert pipeline["decision"]["kind"] == "noop"
    service._run_autonomous_initiative_entry_check.assert_not_called()
    service._run_wake_policy_observations.assert_not_called()
    current_input = ServiceInputPipelineMixin._build_current_input(
        service, input_text=input_text, trigger_kind="wake", interaction_context=None,
    )
    assert current_input.sender_kind == "system"
    assert current_input.source_kind == "wake"
    assert current_input.response_target_refs == ()
    assert current_input.text.endswith(message)


def test_wake_without_message_preserves_empty_wake_request():
    service = _WakeApi()
    service.trigger_wake("test-token", {})
    call = service._execute_wake_cycle.call_args.kwargs
    assert call["wake_message"] is None
    assert call["reference_payload"] is None
    assert call["client_context"] == {}
