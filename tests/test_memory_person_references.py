from copy import deepcopy
import json
from unittest.mock import Mock

import pytest

from otomekairo.llm.client import LLMClient
from otomekairo.llm.contexts import PersonaContext
from otomekairo.llm.contracts import (
    LLMError, known_person_refs_from_context, validate_known_person_references,
    validate_memory_interpretation_contract,
)


@pytest.mark.parametrize('payload', [
    {'candidate_memory_units': [{'subject_hint': 'person:相沢'}]},
    {'candidate_memory_units': [{'subject_hint': ' person:相沢 '}]},
    {'episode': {'primary_scope_key': 'person:相沢'}},
    {'episode_affects': [{'target_scope_key': 'self|person:相沢'}]},
    {'candidate_memory_units': [{'qualifiers_hint': {'participant_refs': ['person:相沢']}}]},
])
def test_name_derived_person_references_are_rejected(payload):
    with pytest.raises(LLMError, match='入力のperson_ref'):
        validate_known_person_references(payload, person_refs={'person:a', 'person:b'})


def test_same_display_name_does_not_merge_structured_person_references():
    refs = known_person_refs_from_context({'people_context': [
        {'person_ref': 'person:a', 'display_name': '相沢'},
        {'person_ref': 'person:b', 'display_name': '相沢'},
    ]})
    assert refs == {'person:a', 'person:b'}
    validate_known_person_references({'candidate_memory_units': [
        {'subject_hint': 'person:a', 'object_hint': {'kind': 'value', 'value': 'ジャスミン茶'}},
        {'subject_hint': 'person:b', 'object_hint': {'kind': 'value', 'value': 'ブラックコーヒー'}},
    ]}, person_refs=refs)


def test_memory_summary_invalid_object_reference_names_its_field():
    payload = {'episode': {
        'episode_type': 'conversation', 'episode_series_id': None,
        'primary_scope_type': 'entity', 'primary_scope_key': 'person:a',
        'summary_text': '好みの訂正を受け取った。', 'outcome_text': None,
        'open_loops': [], 'salience': 0.4,
    }, 'candidate_memory_units': [{
        'memory_type': 'summary', 'scope': 'entity', 'subject_hint': 'person:a',
        'predicate_hint': 'long_term_pattern', 'object_hint': {'kind': 'reference', 'value': 'entity:person:a:summary'},
        'qualifiers_hint': {}, 'summary_text': '普段はほうじ茶が好き。',
        'evidence_text': '本人の訂正。', 'confidence_hint': 'high',
    }], 'episode_affects': []}
    with pytest.raises(LLMError, match=r'candidate_memory_unit\.object_hint'):
        validate_memory_interpretation_contract(payload)
    payload['candidate_memory_units'][0]['object_hint'] = None
    validate_memory_interpretation_contract(payload)


@pytest.mark.parametrize('correct_after_repair', [True, False])
def test_memory_interpretation_repairs_an_unknown_person_before_returning(monkeypatch, correct_after_repair):
    valid = {'episode': {
        'episode_type': 'conversation', 'episode_series_id': None,
        'primary_scope_type': 'entity', 'primary_scope_key': 'person:a',
        'summary_text': '本人が普段の飲み物を伝えた。', 'outcome_text': None,
        'open_loops': [], 'salience': 0.4,
    }, 'candidate_memory_units': [{
        'memory_type': 'preference', 'scope': 'entity', 'subject_hint': 'person:a',
        'predicate_hint': 'preferred_drink', 'object_hint': {'kind': 'value', 'value': 'ジャスミン茶'},
        'qualifiers_hint': '{}', 'summary_text': '普段はジャスミン茶が好き。',
        'evidence_text': '本人が明示した。', 'confidence_hint': 'high',
    }], 'episode_affects': []}
    invalid = deepcopy(valid)
    invalid['candidate_memory_units'][0]['subject_hint'] = 'person:相沢'
    completion = Mock(side_effect=[json.dumps(invalid), json.dumps(valid if correct_after_repair else invalid)])
    monkeypatch.setattr('otomekairo.llm.client.complete_text', completion)
    def generate():
        return LLMClient().generate_memory_interpretation(
        model_config={'model': 'provider/test'},
        persona_context=PersonaContext('Test', 'テスト人格。', None, '判断の基底。'),
        input_text='普段はジャスミン茶が好き。', recall_hint={}, decision={'kind': 'speech'},
        speech_text=None, memory_context={'current_input': {'sender_ref': 'person:a'}},
        current_time='2026-10-04T09:00:00+09:00',
        )
    if not correct_after_repair:
        with pytest.raises(LLMError, match='入力のperson_ref'):
            generate()
        assert completion.call_count == 2
        return
    result = generate()
    assert completion.call_count == 2
    assert result['candidate_memory_units'][0]['subject_hint'] == 'person:a'
