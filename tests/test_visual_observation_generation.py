import json
from unittest.mock import patch

import pytest

from otomekairo.llm.client import LLMClient
from otomekairo.llm.contexts import PersonaContext
from otomekairo.llm.contracts import LLMError


PERSONA = PersonaContext("Test", "観測を確かめて判断する。", None, "判断の基底。")
OBSERVATION = {
    "summary_text": "白い縦長の容器に赤い花が見える。容器の用途は判別できない。",
    "confidence_hint": "medium",
    "change_state": "first_seen",
    "change_basis": "no_previous_observation",
    "change_reason_summary": "同じsourceの前回観測がない。",
}
IMAGE = "data:image/jpeg;base64,fixture"


@pytest.mark.parametrize("image_input_kind", [
    "conversation_attachment", "vision_capture_result", "wake_reference",
])
def test_visual_observation_returns_validated_result_in_one_completion(image_input_kind):
    images = [IMAGE, "data:image/png;base64,second-fixture"]
    source = {"image_input_kind": image_input_kind}
    with patch("otomekairo.llm.client.complete_text", return_value=json.dumps(OBSERVATION)) as complete:
        result = LLMClient().generate_visual_observation_summary(
            model_config={"model": "test"}, persona_context=PERSONA,
            source_pack=source, images=images,
        )

    assert result == OBSERVATION
    complete.assert_called_once()
    request = complete.call_args.kwargs
    assert request["response_format"]["json_schema"]["name"] == "visual_observation"
    content = request["messages"][1]["content"]
    assert [part["image_url"]["url"] for part in content if part["type"] == "image_url"] == images
    assert source == {"image_input_kind": image_input_kind}


def test_visual_observation_preserves_complete_source_context():
    previous = "以前の画像の説明。" * 100
    with patch("otomekairo.llm.client.complete_text", return_value=json.dumps(OBSERVATION)) as complete:
        LLMClient().generate_visual_observation_summary(
            model_config={"model": "test"}, persona_context=PERSONA,
            source_pack={"change_context": {"previous_observation_context": {"summary_text": previous}}},
            images=[IMAGE],
        )

    complete.assert_called_once()
    content = complete.call_args.kwargs["messages"][1]["content"]
    assert any(previous in part["text"] for part in content if part["type"] == "text")


def test_visual_observation_repairs_malformed_json_before_returning_result():
    with patch("otomekairo.llm.client.complete_text", side_effect=["{invalid", json.dumps(OBSERVATION)]) as complete:
        result = LLMClient().generate_visual_observation_summary(
            model_config={"model": "test"}, persona_context=PERSONA,
            source_pack={"image_input_kind": "conversation_attachment"}, images=[IMAGE],
        )

    assert result == OBSERVATION
    assert complete.call_count == 2


def test_visual_observation_fails_when_repaired_result_still_violates_contract():
    invalid = {**OBSERVATION, "summary_text": ""}
    with patch("otomekairo.llm.client.complete_text", return_value=json.dumps(invalid)) as complete:
        with pytest.raises(LLMError):
            LLMClient().generate_visual_observation_summary(
                model_config={"model": "test"}, persona_context=PERSONA,
                source_pack={"image_input_kind": "conversation_attachment"}, images=[IMAGE],
            )

    assert complete.call_count == 2


def test_visual_observation_propagates_generation_failure():
    with patch("otomekairo.llm.client.complete_text", side_effect=LLMError("生成失敗")) as complete:
        with pytest.raises(LLMError):
            LLMClient().generate_visual_observation_summary(
                model_config={"model": "test"}, persona_context=PERSONA,
                source_pack={"image_input_kind": "conversation_attachment"}, images=[IMAGE],
            )

    complete.assert_called_once()
