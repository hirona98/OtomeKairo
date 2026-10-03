"""Direct event evidence keeps the LLM-selected actor and conversation scope."""
from pathlib import Path

import pytest

from otomekairo.evidence import EvidenceResolver
from otomekairo.llm.contracts import LLMError, validate_answer_contract_contract
from otomekairo.store.file_store import SQLiteMemoryStore


def event(event_id, person, room, minute, *, cycle='cycle:shared', role='person'):
    return {
        'event_id': event_id, 'cycle_id': cycle, 'memory_set_id': 'memory_set:default',
        'kind': 'conversation_input' if role == 'person' else 'speech', 'role': role,
        'text': event_id, 'speaker_ref': person if role == 'person' else 'self',
        'interaction_ref': room, 'participant_refs': [person],
        'display_name': '同じ名前', 'created_at': f'2026-10-03T09:{minute:02}:00+09:00',
    }


def contract(*, kind='exact_boundary', boundary='latest', person='person:a', room='interaction:room'):
    return {
        'contract': kind, 'reason_codes': [], 'boundary': boundary, 'target_actor': 'person',
        'target_person_ref': person, 'target_interaction_ref': room, 'query_terms': [],
    }


def resolve(store, answer_contract):
    return EvidenceResolver(store=store).build_evidence_resolution(
        memory_set_id='memory_set:default', augmented_query_text='対象の記録を確認する。',
        recall_pack={}, answer_contract=answer_contract, current_time='2026-10-04T21:00:00+09:00',
    )


@pytest.mark.parametrize('kind,boundary', [
    ('exact_boundary', 'first'), ('exact_boundary', 'latest'),
    ('exact_statement', 'first'), ('exact_statement', 'latest'),
])
def test_boundary_and_cycle_preserve_scope(tmp_path: Path, kind, boundary):
    store = SQLiteMemoryStore(tmp_path)
    store.append_events(events=[
        event('event:other-earlier', 'person:b', 'interaction:room', 0),
        event('event:target', 'person:a', 'interaction:room', 1),
        event('event:other-later', 'person:b', 'interaction:room', 2),
        event('event:other-room', 'person:a', 'interaction:other', 3),
    ])
    result = resolve(store, contract(kind=kind, boundary=boundary))
    items = result['evidence_pack']['evidence_items']
    assert [r['event_id'] for r in items] == ['event:target']
    assert items[0]['speaker_ref'] == 'person:a'
    assert items[0]['interaction_ref'] == 'interaction:room'
    assert result['fact_resolution_trace']['query']['target_person_ref'] == 'person:a'


def test_search_does_not_widen_an_empty_scope(tmp_path: Path):
    store = SQLiteMemoryStore(tmp_path)
    store.append_events(events=[event('event:other', 'person:b', 'interaction:room', 1)])
    for kind, boundary in [('exact_boundary', 'latest'), ('exact_statement', 'none')]:
        result = resolve(store, contract(kind=kind, boundary=boundary))
        assert result['evidence_pack']['status'] == 'missing'
        assert result['evidence_pack']['evidence_items'] == []


def test_person_scope_spans_rooms_and_includes_addressed_assistant(tmp_path: Path):
    store = SQLiteMemoryStore(tmp_path)
    store.append_events(events=[
        event('event:input', 'person:a', 'interaction:one', 1),
        event('event:reply', 'person:a', 'interaction:two', 2, role='assistant'),
        event('event:other', 'person:b', 'interaction:two', 3, role='assistant'),
    ])
    requested = contract(room=None)
    requested['target_actor'] = 'any'
    assert resolve(store, requested)['evidence_pack']['evidence_items'][0]['event_id'] == 'event:reply'
    requested.update(contract='exact_statement', boundary='none')
    assert {r['event_id'] for r in resolve(store, requested)['evidence_pack']['evidence_items']} == {'event:input', 'event:reply'}


def test_answer_contract_requires_explicit_scope():
    requested = contract()
    validate_answer_contract_contract(requested)
    del requested['target_person_ref']
    with pytest.raises(LLMError):
        validate_answer_contract_contract(requested)
    requested = contract(person='display-name')
    with pytest.raises(LLMError):
        validate_answer_contract_contract(requested)


def test_boundary_time_is_event_occurrence_even_when_speech_mentions_earlier_time(tmp_path: Path):
    store = SQLiteMemoryStore(tmp_path)
    reply = event('event:reply', 'person:a', 'interaction:room', 2, role='assistant')
    reply['text'] = '前回お話ししたのは昨日、10月2日です。'
    store.append_events(events=[reply])
    requested = contract()
    requested['target_actor'] = 'any'
    pack = resolve(store, requested)['evidence_pack']
    assert pack['boundary_at'] == '2026-10-03T09:02:00+09:00'
    assert pack['evidence_items'][0]['text'] == reply['text']
