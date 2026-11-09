import json
from identifier_fixtures import id_allocator
from unittest.mock import patch

import pytest

from observation_fixtures import IMAGE, STAMP, image_context, input_payload, text_payloads
from otomekairo.llm.client import LLMClient
from otomekairo.llm.contexts import CurrentInput, PersonaContext
from otomekairo.llm.contracts import LLMError
from otomekairo.observations.acquisition import ObservationAcquisition

PERSONA = PersonaContext("Test", "観測を確かめて判断する。", None, "判断の基底。")
ROW = {"observation_ref": "visual_observation:1", "summary_text": "白い縦長の容器に赤い花が見える。中身は判別できない。",
       "confidence_hint": "medium", "change_state": "first_seen", "change_basis": "no_previous_observation",
       "change_reason_summary": "同じ取得元の前回観測がない。"}


def generate(context, *, recent_turns=None):
    return LLMClient().generate_input_interpretation(
        model_config={"model": "provider/test"}, persona_context=PERSONA,
        current_input=CurrentInput("person", "person:test", "user_message", ("person:test",), None, "何が見える？"),
        input_text="何が見える？", recent_turns=recent_turns or [], current_time=STAMP, observations=context,
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
    surroundings = next(value for value in text_payloads(request["messages"]) if "observation_comparisons" in value)
    assert surroundings["observation_comparisons"] == [{
        "observation_ref": "visual_observation:1", "source_identity_changed": False,
        "previous_observation_context": None, "last_prompted_observation_context": None,
    }]


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


def test_input_interpretation_repairs_missing_comparison_per_observation_with_recent_conversation():
    acquisition = ObservationAcquisition("memory_set:test", 300, id_allocator())
    previous = {"summary_text": "机と椅子が見える。", "observed_at": STAMP}
    for kind in ("desktop", "camera"):
        acquisition.add_image(
            data_uri=IMAGE, source_ref=f"vision_source:{kind}", source_kind=kind, source_label=kind,
            source_owner="self", place_ref=None, origin_kind="vision_capture", observed_at=STAMP,
            received_at=STAMP, time_basis="captured", target={},
            comparison={"previous_observation_context": previous, "same_sample": False} if kind == "desktop" else None,
        )
    context = acquisition.freeze(snapshot_at=STAMP, sensor_observations=(), sensor_reports=())
    stable = {**ROW, "change_state": "stable", "change_basis": "semantic_stability",
              "change_reason_summary": "前回と主要な配置は同じ。"}
    invalid = input_payload(observations=[stable, {**stable, "observation_ref": "visual_observation:2"}])
    corrected_row = {**ROW, "observation_ref": "visual_observation:2"}
    corrected = input_payload(observations=[stable, corrected_row])
    turns = [{"role": "assistant", "text": "カメラには机と椅子が見えます。", "created_at": STAMP}]
    with patch("otomekairo.llm.client.complete_text", side_effect=[json.dumps(invalid), json.dumps(corrected)]) as complete:
        result = generate(context, recent_turns=turns)
    assert result["observation_interpretations"] == [stable, corrected_row]
    assert complete.call_count == 2
    requests = [call.kwargs for call in complete.call_args_list]
    assert requests[0]["response_format"] == requests[1]["response_format"]
    for request in requests:
        values = list(text_payloads(request["messages"]))
        assert next(value for value in values if "recent_turns" in value)["recent_turns"] == turns
        comparisons = next(value for value in values if "observation_comparisons" in value)["observation_comparisons"]
        assert comparisons == [
            {"observation_ref": "visual_observation:1", "source_identity_changed": False,
             "previous_observation_context": previous, "last_prompted_observation_context": None, "same_sample": False},
            {"observation_ref": "visual_observation:2", "source_identity_changed": False,
             "previous_observation_context": None, "last_prompted_observation_context": None},
        ]
        image_parts = [part for message in request["messages"] if isinstance(message["content"], list)
                       for part in message["content"] if part["type"] == "image_url"]
        assert [part["image_url"]["url"] for part in image_parts] == [IMAGE, IMAGE]
    repair = requests[1]["messages"][-1]["content"]
    assert "visual_observation:2" in repair
    assert "previous_observation_context=null" in repair
    assert "change_state=first_seen, change_basis=no_previous_observation" in repair
    acquisition.close()


@pytest.mark.parametrize("comparisons,invalid_state,invalid_basis,expected_state,expected_basis", [
    ({"previous_observation_context": {"summary_text": "前回の画像の説明。"}},
     "first_seen", "no_previous_observation", "stable", "semantic_stability"),
    ({"source_identity_changed": True},
     "first_seen", "no_previous_observation", "changed", "source_identity_changed"),
    ({"previous_observation_context": {"summary_text": "前回の画像の説明。"}},
     "changed", "source_identity_changed", "changed", "semantic_change"),
    ({"previous_observation_context": {"summary_text": "前回の画像の説明。"}},
     "same_as_recent_speech", "recent_speech_repetition", "stable", "semantic_stability"),
])
def test_input_interpretation_repairs_comparison_prerequisites(comparisons, invalid_state, invalid_basis, expected_state, expected_basis):
    context = image_context(comparisons=comparisons)
    invalid = input_payload(observations=[{**ROW, "change_state": invalid_state, "change_basis": invalid_basis}])
    corrected = {**ROW, "change_state": expected_state, "change_basis": expected_basis}
    with patch("otomekairo.llm.client.complete_text", side_effect=[json.dumps(invalid), json.dumps(input_payload(observations=[corrected]))]) as complete:
        assert generate(context)["observation_interpretations"] == [corrected]
    assert complete.call_count == 2
    repair = complete.call_args.kwargs["messages"][-1]["content"]
    assert "visual_observation:1" in repair
    assert "source_identity_changed" in repair


def test_input_interpretation_accepts_recent_speech_comparison_when_both_materials_exist():
    previous = {"summary_text": "机と椅子が見える。"}
    context = image_context(comparisons={"previous_observation_context": previous,
                                         "last_prompted_observation_context": previous})
    row = {**ROW, "change_state": "same_as_recent_speech", "change_basis": "recent_speech_repetition"}
    with patch("otomekairo.llm.client.complete_text", return_value=json.dumps(input_payload(observations=[row]))) as complete:
        assert generate(context)["observation_interpretations"] == [row]
    complete.assert_called_once()
    surroundings = next(value for value in text_payloads(complete.call_args.kwargs["messages"]) if "observation_comparisons" in value)
    assert surroundings["observation_comparisons"][0]["last_prompted_observation_context"] == previous


@pytest.mark.parametrize("rows", [[], [ROW, ROW], [{**ROW, "observation_ref": "unknown"}], [{**ROW, "summary_text": ""}],
                                 [{**ROW, "change_state": "stable", "change_basis": "semantic_stability"}]])
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
