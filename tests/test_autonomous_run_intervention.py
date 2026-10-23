import json
from unittest.mock import patch

import pytest

from otomekairo.llm.autonomous_run_intervention import validate_intervention
from otomekairo.llm.client import LLMClient
from otomekairo.llm.contexts import build_persona_context
from otomekairo.llm.contracts import LLMError


def generate(context):
    return LLMClient().generate_autonomous_run_intervention(
        model_config={"model": "real-test"},
        persona_context=build_persona_context({"persona_prompt": "テスト人格。"}, role="autonomous_run_intervention"),
        context=context,
    )


def context():
    return {
        "current_input": {"sender_kind": "person", "source_kind": "user_message", "text": "読み取りは依頼ではなかったよ"},
        "recent_turns": [{"role": "assistant", "text": "読み取りを続けます。"}],
        "autonomous_runs": [{"run_id": "autonomous_run:sensor", "objective_summary": "センサを読み取る。"}],
    }


def test_cancellation_uses_complete_context_and_repairs_an_unknown_target():
    valid = {"cancel_run_ids": ["autonomous_run:sensor"], "reason_summary": "作業の依頼として受け取った前提が訂正された。"}
    invalid = {**valid, "cancel_run_ids": ["autonomous_run:unknown"]}
    supplied = context()
    with patch("otomekairo.llm.client.complete_text", side_effect=[json.dumps(invalid), json.dumps(valid)]) as complete:
        assert generate(supplied) == valid
    assert complete.call_count == 2
    data = json.loads(complete.call_args_list[0].kwargs["messages"][1]["content"].splitlines()[1])
    assert {key: data[key] for key in supplied} == supplied


def test_repeated_generation_failure_does_not_silently_keep_running():
    with patch("otomekairo.llm.client.complete_text", return_value='{}') as complete:
        with pytest.raises(LLMError):
            generate(context())
    assert complete.call_count == 2


@pytest.mark.parametrize("source", [
    {"sender_kind": "system", "source_kind": "wake"},
    {"sender_kind": "system", "source_kind": "capability_result"},
])
def test_observation_cannot_cancel_runs_as_a_person_message(source):
    supplied = context()
    supplied["current_input"].update(source)
    with patch("otomekairo.llm.client.complete_text") as complete:
        with pytest.raises(LLMError):
            generate(supplied)
    complete.assert_not_called()


@pytest.mark.parametrize("targets", [["autonomous_run:unknown"], ["autonomous_run:sensor"] * 2, "autonomous_run:sensor"])
def test_invalid_target_references_are_explicit_failures(targets):
    with pytest.raises(LLMError):
        validate_intervention({"cancel_run_ids": targets, "reason_summary": "取消対象。"}, run_ids={"autonomous_run:sensor"})
