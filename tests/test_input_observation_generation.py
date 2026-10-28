import json
from unittest.mock import patch

import pytest

from observation_fixtures import IMAGE, STAMP, image_context, input_payload
from otomekairo.llm.client import LLMClient
from otomekairo.llm.contexts import CurrentInput, PersonaContext
from otomekairo.llm.contracts import LLMError

PERSONA = PersonaContext("Test", "観測を確かめて判断する。", None, "判断の基底。")
ROW = {"observation_ref": "observation:1", "summary_text": "白い縦長の容器に赤い花が見える。中身は判別できない。",
       "confidence_hint": "medium", "change_state": "first_seen", "change_basis": "no_previous_observation",
       "change_reason_summary": "同じ取得元の前回観測がない。"}


def generate(context):
    return LLMClient().generate_input_interpretation(
        model_config={"model": "provider/test"}, persona_context=PERSONA,
        current_input=CurrentInput("person", "person:test", "user_message", ("person:test",), None, "何が見える？"),
        input_text="何が見える？", recent_turns=[], current_time=STAMP, observations=context,
    )


@pytest.mark.parametrize("origin_kind", ["conversation_attachment", "vision_capture", "wake_reference"])
def test_input_interpretation_reads_image_and_returns_observations_in_one_completion(origin_kind):
    context = image_context(origin_kind=origin_kind)
    with patch("otomekairo.llm.client.complete_text", return_value=json.dumps(input_payload(observations=[ROW]))) as complete:
        result = generate(context)
    assert result["observation_interpretations"] == [ROW]
    complete.assert_called_once()
    request = complete.call_args.kwargs
    assert request["response_format"]["json_schema"]["name"] == "input_interpretation"
    image_parts = [part for message in request["messages"] if isinstance(message["content"], list)
                   for part in message["content"] if part["type"] == "image_url"]
    assert [part["image_url"]["url"] for part in image_parts] == [IMAGE]


def test_input_interpretation_preserves_complete_comparison_material():
    previous = "以前の画像の説明。" * 100
    context = image_context(comparisons={"previous_observation_context": {"summary_text": previous}})
    with patch("otomekairo.llm.client.complete_text", return_value=json.dumps(input_payload(observations=[{**ROW, "change_state": "changed", "change_basis": "semantic_change"}]))) as complete:
        generate(context)
    texts = [part["text"] for message in complete.call_args.kwargs["messages"] if isinstance(message["content"], list)
             for part in message["content"] if part["type"] == "text"]
    assert any(previous in text for text in texts)


def test_input_interpretation_repairs_json_with_the_same_image():
    context = image_context()
    with patch("otomekairo.llm.client.complete_text", side_effect=["{invalid", json.dumps(input_payload(observations=[ROW]))]) as complete:
        assert generate(context)["observation_interpretations"] == [ROW]
    assert complete.call_count == 2
    for call in complete.call_args_list:
        parts = [part for message in call.kwargs["messages"] if isinstance(message["content"], list)
                 for part in message["content"] if part["type"] == "image_url"]
        assert [part["image_url"]["url"] for part in parts] == [IMAGE]


@pytest.mark.parametrize("rows", [[], [ROW, ROW], [{**ROW, "observation_ref": "unknown"}], [{**ROW, "summary_text": ""}]])
def test_invalid_observation_contract_fails_after_one_repair(rows):
    with patch("otomekairo.llm.client.complete_text", return_value=json.dumps(input_payload(observations=rows))) as complete:
        with pytest.raises(LLMError):
            generate(image_context())
    assert complete.call_count == 2


def test_generation_failure_does_not_remove_the_image_or_change_roles():
    with patch("otomekairo.llm.client.complete_text", side_effect=LLMError("生成失敗")) as complete:
        with pytest.raises(LLMError):
            generate(image_context())
    complete.assert_called_once()
