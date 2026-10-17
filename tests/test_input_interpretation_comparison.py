from copy import deepcopy
import json
from pathlib import Path
import sys
from unittest.mock import Mock

import pytest

from otomekairo.llm.client import LLMClient
from otomekairo.llm.contexts import CurrentInput, build_persona_context
from otomekairo.llm.contracts import LLMError
from otomekairo.llm.schemas import input_interpretation_response_format
from otomekairo.store.config import ConfigStore

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from compare_input_interpretation import combined_response_format, generate_combined, main, read_selected_settings


def interpretation():
    return {
        "recall_hint": {"primary_recall_focus": "fact", "secondary_recall_focuses": [], "confidence": 0.8,
                        "time_reference": "none", "focus_scopes": [], "mentioned_entities": [],
                        "mentioned_topics": [], "risk_flags": []},
        "answer_contract": {"contract": "summary", "reason_codes": [], "boundary": "none", "target_actor": "any",
                            "target_person_ref": None, "target_interaction_ref": None, "query_terms": []},
    }


def arguments(text="今は自宅にいる。"):
    return {
        "model_config": {"model": "provider/test"},
        "persona_context": build_persona_context({"persona_prompt": "入力の根拠を確かめる。"}, role="input_interpretation"),
        "input_text": text, "current_input": CurrentInput("person", "person:test", "user_message", ("person:test",), None, text),
        "recent_turns": [], "current_time": "2026-10-17T09:00:00+09:00",
        "current_visual_observations": [], "activity_context": None,
    }


def test_prototype_uses_one_call_and_preserves_full_input_and_existing_contracts(monkeypatch):
    text = "今回の本人報告。" * 200 + "今は自宅にいる。"
    payload = {**interpretation(), "reported_states": [{"state_type": "location", "evidence_text": "今は自宅にいる。"}]}
    completion = Mock(return_value=json.dumps(payload, ensure_ascii=False))
    monkeypatch.setattr("otomekairo.llm.client.complete_text", completion)
    result = generate_combined(LLMClient(), **arguments(text))
    expected = deepcopy(payload)
    expected["answer_contract"]["requires_direct_evidence"] = False
    assert result == expected
    assert completion.call_count == 1
    request = completion.call_args.kwargs
    assert request["response_format"]["json_schema"]["name"] == "input_interpretation_with_world_reports"
    assert text in request["messages"][-1]["content"]
    assert set(combined_response_format()["json_schema"]["schema"]["required"]) == set(payload)
    assert set(input_interpretation_response_format()["json_schema"]["schema"]["required"]) == {"recall_hint", "answer_contract"}


@pytest.mark.parametrize("invalid", [
    [{"state_type": "location", "evidence_text": "画像だけから得た場所。"}],
    [{"state_type": "activity", "evidence_text": "今は自宅にいる。"}],
    [{"state_type": "location", "evidence_text": "今は自宅にいる。"}] * 2,
])
def test_invalid_reports_fail_explicitly_after_one_repair(monkeypatch, invalid):
    completion = Mock(return_value=json.dumps({**interpretation(), "reported_states": invalid}, ensure_ascii=False))
    monkeypatch.setattr("otomekairo.llm.client.complete_text", completion)
    with pytest.raises(LLMError):
        generate_combined(LLMClient(), **arguments())
    assert completion.call_count == 2


def test_normal_input_generation_keeps_the_current_two_field_response(monkeypatch):
    completion = Mock(return_value=json.dumps(interpretation()))
    monkeypatch.setattr("otomekairo.llm.client.complete_text", completion)
    expected = interpretation()
    expected["answer_contract"]["requires_direct_evidence"] = False
    assert LLMClient().generate_input_interpretation(**arguments()) == expected
    assert completion.call_args.kwargs["response_format"]["json_schema"]["name"] == "input_interpretation"


def test_non_person_input_is_rejected_without_a_provider_call(monkeypatch):
    kwargs = arguments()
    kwargs["current_input"] = CurrentInput("system", None, "wake", (), None, kwargs["input_text"])
    completion = Mock()
    monkeypatch.setattr("otomekairo.llm.client.complete_text", completion)
    with pytest.raises(ValueError, match="人物発話"):
        generate_combined(LLMClient(), **kwargs)
    completion.assert_not_called()


@pytest.mark.parametrize("outcome,repair,expected", [("pass", False, 0), ("fail", False, 1),
                                                     ("inconclusive", False, 1), ("pass", True, 1)])
def test_comparison_counts_baseline_prototype_and_judge_separately(tmp_path, monkeypatch, outcome, repair, expected):
    store = ConfigStore(tmp_path / "config")
    state = store.read_state()
    state["model_presets"][state["selected_model_preset_id"]]["model"] = "provider/test"
    store.write_state(state)
    db_path = store.config_db_path
    before = db_path.read_bytes()
    persona, model = read_selected_settings(tmp_path / "config")
    assert persona == state["personas"][state["selected_persona_id"]]
    assert model["model"] == "provider/test"
    assert db_path.read_bytes() == before
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps([{"case_id": "question", "text": "今の状態を教えて。", "expected": "質問だけなら報告は空。"}]), encoding="utf-8")
    combined_attempts = 0

    def provider(**kwargs):
        nonlocal combined_attempts
        schema = kwargs["response_format"]["json_schema"]["name"]
        if schema == "input_interpretation":
            payload = interpretation()
        elif schema == "world_state_source_selection":
            payload = {"reported_states": []}
        elif schema == "input_interpretation_with_world_reports":
            combined_attempts += 1
            reports = [{"state_type": "location", "evidence_text": "出所のない引用。"}] if repair and combined_attempts == 1 else []
            payload = {**interpretation(), "reported_states": reports}
        elif schema == "input_interpretation_comparison":
            payload = {key: {"outcome": outcome, "reason_summary": "制御された評価fixture。"}
                       for key in ("baseline", "combined", "meaning_preserved")}
        else:
            raise AssertionError(schema)
        return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(payload, ensure_ascii=False)}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}

    monkeypatch.setattr("otomekairo.llm.transport._load_litellm_completion", lambda: provider)
    artifact = tmp_path / "result"
    assert main(["--config-data-dir", str(tmp_path / "config"), "--cases-file", str(cases),
                 "--artifact-dir", str(artifact), "--repetitions", "1"]) == expected
    result = json.loads((artifact / "comparison.json").read_text())
    assert result["production_adoption"] is False
    assert result["comparison_passed"] is (expected == 0)
    assert result["calls"]["baseline"]["provider_calls"] == 2
    assert result["calls"]["combined"]["provider_calls"] == (2 if repair else 1)
    assert result["calls"]["evaluation"]["provider_calls"] == 1
    assert result["calls"]["baseline"]["total_tokens"] == 30
    assert db_path.read_bytes() == before


def test_dry_run_does_not_read_settings_or_make_calls(monkeypatch, capsys):
    reader = Mock(side_effect=AssertionError("settings should not be read"))
    monkeypatch.setattr("compare_input_interpretation.read_selected_settings", reader)
    assert main(["--dry-run"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["api_calls"] == 0
    assert result["comparison_passed"] is False
    reader.assert_not_called()
