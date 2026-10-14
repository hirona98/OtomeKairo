import json
from copy import deepcopy
from unittest.mock import patch

import pytest

from otomekairo.llm.client import LLMClient
from otomekairo.llm.contexts import PersonaContext
from otomekairo.llm.contracts import LLMError
from otomekairo.llm.schemas import activity_state_response_format
from otomekairo.service.app import OtomeKairoService


PERSONA = PersonaContext("Test", "根拠を確かめて判断する。", None, "判断の基底。")
SOURCE = {
    "activity_subject": {"actor": "person", "actor_ref": "person:test"},
    "observed_person_refs": [],
    "current_input": {"sender_kind": "person", "sender_ref": "person:test", "text": "文書を整理している。"},
}
CANDIDATE = {
    "actor": "person", "label": "文書を整理", "target": "文書",
    "reason_summary": "本人の現在の作業の報告に支えられる。", "evidence_kind": "supported_report",
    "confidence_hint": "high", "salience_hint": "medium", "ttl_hint": "short", "transition": "start",
}


def _generate(source_pack):
    return LLMClient().generate_activity_state(
        model_config={"model": "test"}, persona_context=PERSONA, source_pack=source_pack,
    )


@pytest.mark.parametrize("evidence_kind,accepted", [
    ("supported_report", True), ("supported_observation", True),
    ("question", False), ("assistant_activity", False), ("persona_setting", False),
    ("inference", False), ("none", False),
])
def test_generation_returns_only_supported_candidates_in_one_completion(evidence_kind, accepted):
    candidate = {**CANDIDATE, "evidence_kind": evidence_kind}
    source = deepcopy(SOURCE)
    with patch("otomekairo.llm.client.complete_text", return_value=json.dumps({"activity_candidates": [candidate]})) as complete:
        result = _generate(source)

    complete.assert_called_once()
    assert complete.call_args.kwargs["response_format"] == activity_state_response_format()
    assert result == {"activity_candidates": [candidate] if accepted else []}
    assert source == SOURCE


@pytest.mark.parametrize("owner,refs,evidence_kind,accepted", [
    ("self", [], "supported_observation", False),
    ("self", ["person:other"], "supported_observation", False),
    ("self", ["person:test"], "supported_observation", True),
    ("self", [], "supported_report", True),
    ("user_environment", [], "supported_observation", True),
])
def test_camera_observation_requires_matching_person_reference(owner, refs, evidence_kind, accepted):
    candidate = {**CANDIDATE, "evidence_kind": evidence_kind}
    source = {**SOURCE, "source_owner": owner, "observed_person_refs": refs}
    with patch("otomekairo.llm.client.complete_text", return_value=json.dumps({"activity_candidates": [candidate]})) as complete:
        result = _generate(source)

    complete.assert_called_once()
    assert result == {"activity_candidates": [candidate] if accepted else []}


@pytest.mark.parametrize("invalid", [
    {key: value for key, value in CANDIDATE.items() if key != "evidence_kind"},
    {**CANDIDATE, "evidence_kind": "unknown"},
    {**CANDIDATE, "evidence_kind": []},
    {**CANDIDATE, "evidence_kind": "question", "reason_summary": ""},
])
def test_invalid_grounding_fields_fail_after_repair(invalid):
    with patch("otomekairo.llm.client.complete_text", return_value=json.dumps({"activity_candidates": [invalid]})) as complete:
        with pytest.raises(LLMError):
            _generate(SOURCE)

    assert complete.call_count == 2


def test_empty_candidates_need_only_one_completion():
    with patch("otomekairo.llm.client.complete_text", return_value=json.dumps({"activity_candidates": []})) as complete:
        result = _generate({"source_owner": "self"})

    complete.assert_called_once()
    assert result == {"activity_candidates": []}


def test_generation_repairs_malformed_json():
    with patch("otomekairo.llm.client.complete_text", side_effect=["{invalid", json.dumps({"activity_candidates": [CANDIDATE]})]) as complete:
        result = _generate(SOURCE)

    assert complete.call_count == 2
    assert result == {"activity_candidates": [CANDIDATE]}


def test_generation_failure_propagates():
    with patch("otomekairo.llm.client.complete_text", side_effect=LLMError("生成失敗")) as complete:
        with pytest.raises(LLMError):
            _generate(SOURCE)

    complete.assert_called_once()


def test_rejected_candidate_keeps_stored_activity_and_expiration(tmp_path):
    service = OtomeKairoService(tmp_path)
    state = service.store.read_state()
    memory_set_id = state["selected_memory_set_id"]

    def refresh(text, current_time, candidate):
        with patch("otomekairo.llm.client.complete_text", return_value=json.dumps({"activity_candidates": [candidate]})) as complete:
            result = service._refresh_activity_context(
                state=state, started_at=current_time, input_text=text,
                current_input={"sender_kind": "person", "sender_ref": "person:test", "source_kind": "user_message", "text": text},
                recent_turns=[], trigger_kind="user_message", client_context={},
                observation_summary=None, visual_observation_context=None, foreground_world_state=None,
                cycle_id="cycle:test", cycle_label="test", persona_context=PERSONA,
            )
        complete.assert_called_once()
        return result

    _, trace = refresh("文書を整理している。", "2026-10-14T12:00:00+09:00", CANDIDATE)
    assert trace["updated_count"] == 1
    previous = service.store.get_current_activity_state(
        memory_set_id=memory_set_id, actor_ref="person:test", current_time="2026-10-14T12:00:00+09:00",
    )
    assert previous["actor_ref"] == "person:test"
    assert previous["label"] == CANDIDATE["label"]
    assert previous["reason_summary"] == CANDIDATE["reason_summary"]
    assert "evidence_kind" not in previous

    _, trace = refresh("何をしていたっけ？", "2026-10-14T12:01:00+09:00", {**CANDIDATE, "evidence_kind": "question"})
    current = service.store.get_current_activity_state(
        memory_set_id=memory_set_id, actor_ref="person:test", current_time="2026-10-14T12:01:00+09:00",
    )
    assert trace["candidate_count"] == 0
    assert trace["updated_count"] == 0
    for key in ("activity_id", "updated_at", "expires_at"):
        assert current[key] == previous[key]
