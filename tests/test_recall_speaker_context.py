import json
from unittest.mock import Mock

import pytest

from otomekairo.llm.client import LLMClient
from otomekairo.llm.contexts import PersonaContext
from otomekairo.recall.builder import RecallBuilder


@pytest.mark.parametrize('current_person_ref', ['person:b', None])
def test_recall_selection_receives_actual_speaker_with_same_name_candidates(monkeypatch, current_person_ref):
    completion = Mock(return_value=json.dumps({
        'section_selection': [{'section_name': 'person_model', 'candidate_refs': ['candidate:person_model:1']}],
        'conflict_summaries': [],
    }))
    monkeypatch.setattr('otomekairo.llm.client.complete_text', completion)
    builder = RecallBuilder(store=Mock(), llm=LLMClient())
    items = [{
        'source_kind': 'memory_unit', 'memory_unit_id': 'memory:' + person, 'retrieval_lane': lane,
        'memory_type': 'preference', 'scope_type': 'entity', 'scope_key': 'person:' + person,
        'summary_text': '相沢さんの普段の飲み物の好み。', 'salience': 0.8, 'status': 'confirmed',
    } for person, lane in [('b', 'structured'), ('a', 'association')]]
    result = builder._select_recall_pack_sections(
        augmented_query_text='僕の普段の飲み物の好みは何だった？',
        current_person_ref=current_person_ref,
        recall_hint={'primary_recall_focus': 'preference', 'focus_scopes': []},
        candidate_sections={'person_model': items}, conflicts=[],
        model_config={'model': 'provider/test'},
        persona_context=PersonaContext('Test', 'テスト人格。', None, '判断の基底。'),
    )
    wire = json.loads(completion.call_args.kwargs['messages'][1]['content'].split('\n')[1])
    assert wire['current_person_ref'] == current_person_ref
    assert [candidate['scope_key'] for candidate in wire['candidate_sections'][0]['candidates']] == ['person:b', 'person:a']
    assert result['sections']['person_model'][0]['memory_unit_id'] == 'memory:b'
