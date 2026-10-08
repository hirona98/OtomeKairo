from copy import deepcopy
import json
from unittest.mock import Mock

import pytest

from otomekairo.llm.client import LLMClient
from otomekairo.llm.contexts import PersonaContext
from otomekairo.llm.contracts import LLMError, validate_memory_interpretation_contract


def interpretation(qualifiers):
    return {
        'episode': {
            'episode_type': 'preference_change', 'episode_series_id': None,
            'primary_scope_type': 'entity', 'primary_scope_key': 'person:a',
            'summary_text': '本人が普段の飲み物の変化を報告した。', 'outcome_text': None,
            'open_loops': [], 'salience': 0.5,
        },
        'candidate_memory_units': [{
            'memory_type': 'preference', 'scope': 'entity', 'subject_hint': 'person:a',
            'predicate_hint': 'preferred_drink', 'object_hint': {'kind': 'value', 'value': '麦茶'},
            'qualifiers_hint': qualifiers, 'summary_text': '今は麦茶が好き。',
            'evidence_text': '本人が以前の好みから変わったと述べた。', 'confidence_hint': 'high',
        }],
        'episode_affects': [],
    }


@pytest.mark.parametrize('key', ['valid_from', 'valid_to', 'confirmed_at', 'source_iso'])
@pytest.mark.parametrize('value', ['2026-10-04', '2026-10-04T09:00:36', '', 42])
def test_memory_timestamp_fields_reject_non_offset_values(key, value):
    with pytest.raises(LLMError, match='offset'):
        validate_memory_interpretation_contract(interpretation({key: value}))


def test_memory_timestamp_fields_accept_offset_values_and_keep_natural_periods():
    payload = interpretation({
        'valid_from': '2026-10-04T09:00:36+09:00', 'valid_to': None,
        'evidence': [{'confirmed_at': '2026-10-04T00:00:36Z'}],
        'period': '10月から、具体的な開始時刻は不明。',
    })
    original = deepcopy(payload)
    validate_memory_interpretation_contract(payload)
    assert payload == original


def test_memory_timestamp_fields_are_validated_inside_nested_qualifiers():
    with pytest.raises(LLMError, match='offset'):
        validate_memory_interpretation_contract(interpretation({'evidence': [{'confirmed_at': '2026-10-04'}]}))


@pytest.mark.parametrize('correct_after_repair', [True, False])
def test_memory_interpretation_repairs_a_date_only_qualifier_before_returning(monkeypatch, correct_after_repair):
    invalid = interpretation(json.dumps({'replace_prior': True, 'valid_from': '2026-10-04'}))
    valid = interpretation(json.dumps({'replace_prior': True, 'valid_from': '2026-10-04T09:00:36+09:00'}))
    completion = Mock(side_effect=[json.dumps(invalid), json.dumps(valid if correct_after_repair else invalid)])
    monkeypatch.setattr('otomekairo.llm.client.complete_text', completion)

    def generate():
        return LLMClient().generate_memory_interpretation(
            model_config={'model': 'provider/test'},
            persona_context=PersonaContext('Test', 'テスト人格。', None, '判断の基底。'),
            input_text='以前はほうじ茶が好きだったけど、これから普段飲みたいのは麦茶。',
            recall_hint={}, decision={'kind': 'speech'}, speech_text=None,
            memory_context={'current_input': {'sender_ref': 'person:a'}},
            current_time='2026-10-04T09:00:36+09:00',
        )

    if not correct_after_repair:
        with pytest.raises(LLMError, match='offset'):
            generate()
    else:
        result = generate()
        assert result['candidate_memory_units'][0]['qualifiers_hint']['valid_from'] == '2026-10-04T09:00:36+09:00'
    assert completion.call_count == 2
