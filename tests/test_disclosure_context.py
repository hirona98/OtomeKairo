"""共有条件の根拠を生成へ渡す経路を確認する。自然文の精度は評価しない。"""
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from otomekairo.llm.client import LLMClient
from otomekairo.llm.contracts import LLMError
from otomekairo.service.input.pipeline import ServiceInputPipelineMixin
from test_decision_contract import _current_input, _decision_context, _persona_context, _wire_response


def _sources():
    event = {
        "event_id": "event:source", "role": "person", "speaker_ref": "person:other",
        "participant_refs": ["person:other"], "interaction_ref": "interaction:other",
        "created_at": "2026-10-03T09:00:00+09:00",
        "text": "  普段は無糖の麦茶が好きです。\nこの好みは今の相手へ共有して構いません。  ",
    }
    pack = {"person_model": [{
        "memory_unit_id": "memory:other", "source_participant_refs": ["person:other"],
        "evidence_event_ids": [event["event_id"]], "linked_event_ids": [event["event_id"]],
        "summary_text": "麦茶の好みと共有条件。",
    }]}
    return event, pack


def test_shared_source_events_are_loaded_once_before_generation_and_kept_complete():
    event, pack = _sources()
    service = ServiceInputPipelineMixin()
    service.store = SimpleNamespace(load_events_for_evidence=Mock(return_value=[event]))
    turns = [{"role": "assistant", "text": "過去の自分の解釈。"}]
    disclosure = service._build_disclosure_context(
        memory_set_id="memory_set:test", current_input=_current_input(),
        recall_pack=pack, recent_turns=turns,
    )
    service.store.load_events_for_evidence.assert_called_once_with(
        memory_set_id="memory_set:test", event_ids=["event:source"], limit=1,
    )
    assert disclosure["person_utterances"] == [event]
    assert disclosure["other_person_sources"] == pack["person_model"]
    assert disclosure["source_refs"] == ["memory:other"]
    assert disclosure["person_utterances"][0]["text"] == event["text"]


def test_missing_sharing_source_is_an_explicit_failure():
    _, pack = _sources()
    service = ServiceInputPipelineMixin()
    service.store = SimpleNamespace(load_events_for_evidence=Mock(return_value=[]))
    with pytest.raises(ValueError, match="共有許可の根拠 event"):
        service._build_disclosure_context(
            memory_set_id="memory_set:test", current_input=_current_input(), recall_pack=pack,
        )


def test_own_sources_need_no_extra_event_lookup():
    service = ServiceInputPipelineMixin()
    service.store = Mock()
    current = _current_input()
    result = service._build_disclosure_context(
        memory_set_id="memory_set:test", current_input=current,
        recall_pack={"person_model": [{"source_participant_refs": list(current.response_target_refs)}]},
    )
    assert result is None
    service.store.load_events_for_evidence.assert_not_called()


@pytest.mark.parametrize("kind", ["speech", "noop"])
def test_sharing_conditions_and_final_text_use_the_same_completion(kind):
    event, pack = _sources()
    service = ServiceInputPipelineMixin()
    service.store = SimpleNamespace(load_events_for_evidence=Mock(return_value=[event]))
    disclosure = service._build_disclosure_context(
        memory_set_id="memory_set:test", current_input=_current_input(), recall_pack=pack,
    )
    context = replace(_decision_context([]), disclosure_context=disclosure, recall_pack=pack)
    candidate = {
        "kind": kind, "speech_text": "検証用の最終本文です。" if kind == "speech" else None,
        "reason_code": "source_scope", "reason_summary": "共有条件に沿って判断する。",
        "requires_confirmation": False, "pending_intent": None, "capability_request": None,
        "autonomous_run": None,
        "foreground_selection": {"primary_factor_ref": None, "supporting_factor_refs": [],
                                 "suppressed_factors": [], "summary_text": "今回の根拠を使う。"},
        "target_stances": [{"target": "outward_speech", "stance": "advance" if kind == "speech" else "hold",
                            "reason_summary": "共有条件を踏まえる。"}],
    }
    with patch("otomekairo.llm.client.complete_text", return_value=_wire_response(candidate)) as completion:
        assert LLMClient().generate_decision(
            model_config={"model": "provider/test"}, persona_context=_persona_context(), context=context,
        ) == candidate
    completion.assert_called_once()
    blocks = completion.call_args.kwargs["messages"]
    source = next(message["content"] for message in blocks
                  if message["content"].startswith("<<<OTOMEKAIRO_DISCLOSURE_CONTEXT>>>"))
    payload = json.loads(source.splitlines()[1])
    assert payload["person_utterances"][0]["text"] == event["text"]
    assert payload["other_person_sources"][0]["source_participant_refs"] == ["person:other"]


@pytest.mark.parametrize("speech_text", [None, "", " \n "])
def test_speech_requires_nonempty_final_text_and_fails_after_one_repair(speech_text):
    from otomekairo.llm.contracts import build_decision_target_stances_for_kind
    from test_decision_contract import _capability_decision
    candidate = _capability_decision("unused", {})
    candidate.update(kind="speech", capability_request=None, speech_text=speech_text,
                     target_stances=build_decision_target_stances_for_kind("speech", required_targets=("outward_speech",), reason_summary="問いに答える。"))
    with patch("otomekairo.llm.client.complete_text", return_value=_wire_response(candidate)) as completion:
        with pytest.raises(LLMError, match="speech_text"):
            LLMClient().generate_decision(
                model_config={"model": "provider/test"}, persona_context=_persona_context(), context=_decision_context([]),
            )
    assert completion.call_count == 2
