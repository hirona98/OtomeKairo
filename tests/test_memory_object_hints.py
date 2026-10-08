from unittest.mock import Mock

import pytest

from otomekairo.llm.contracts import LLMError, validate_known_person_references
from otomekairo.memory.actions import MemoryActionResolver
from otomekairo.memory.utils import memory_claim_context, normalized_memory_object_hint, semantic_qualifiers
from otomekairo.store.entity_registry import StoreEntityRegistryMixin
from otomekairo.store.relation_index import StoreRelationIndexMixin


@pytest.mark.parametrize("body", ["person:bへ仮題のみ共有可能。詳しい内容は共有不可。", "person:unknownについての未同定の報告。"])
def test_reference_prefixed_value_is_not_a_person_identity(body):
    hint = {"kind": "value", "value": body}
    validate_known_person_references({"candidate_memory_units": [{"object_hint": hint}]}, person_refs={"person:a", "person:b"})
    assert normalized_memory_object_hint(hint) == body


def test_explicit_reference_must_match_an_offered_person():
    payload = {"candidate_memory_units": [{"object_hint": {"kind": "reference", "value": "person:b"}}]}
    validate_known_person_references(payload, person_refs={"person:a", "person:b"})
    with pytest.raises(LLMError, match="入力のperson_ref"):
        validate_known_person_references(payload, person_refs={"person:a"})


@pytest.mark.parametrize("hint", ["person:b", {}, {"kind": "unknown", "value": "person:b"},
                                  {"kind": [], "value": "person:b"}, {"kind": "value", "value": ""},
                                  {"kind": "reference", "value": "entity:person:b:summary"}])
def test_untagged_or_invalid_hint_is_an_explicit_failure(hint):
    with pytest.raises(ValueError, match="object_hint"):
        normalized_memory_object_hint(hint)


def test_normalization_and_entity_indexes_preserve_the_value_boundary():
    resolver = MemoryActionResolver(store=Mock())
    body = "person:bへの共有条件。" * 500
    candidate = {
        "memory_type": "commitment", "scope": "topic", "subject_hint": "topic:test",
        "predicate_hint": "sharing_boundary", "object_hint": {"kind": "value", "value": body},
        "qualifiers_hint": {}, "summary_text": "架空の共有条件。", "confidence_hint": "high",
        "evidence_text": "架空の本人報告。",
    }
    unit = resolver._normalized_candidate_memo(candidate, finished_at="2026-10-04T09:00:06+09:00")
    assert unit["object_ref_or_value"] == body
    assert unit["qualifiers"]["object_kind"] == "value"
    assert memory_claim_context(unit)["qualifiers"]["object_kind"] == "value"
    assert "object_kind" not in semantic_qualifiers(unit["qualifiers"])
    assert resolver._candidate_entity_refs(unit) == []
    assert StoreEntityRegistryMixin()._extract_named_entity_refs_from_memory_unit(unit) == []
    relation = {**unit, "memory_type": "relation", "scope_type": "entity", "subject_ref": "person:a"}
    assert StoreRelationIndexMixin()._relation_edge_identity(relation) == (None, "invalid")
    reference = {**relation, "object_ref_or_value": "person:b", "qualifiers": {"object_kind": "reference"}}
    assert set(resolver._candidate_entity_refs(reference)) == {"person:a", "person:b"}
    assert StoreRelationIndexMixin()._relation_edge_identity(reference)[0] == ("person:a", "person:b", "sharing_boundary")
