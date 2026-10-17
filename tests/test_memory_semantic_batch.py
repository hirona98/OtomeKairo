from copy import deepcopy
import math
from unittest.mock import Mock

import pytest

from otomekairo.memory.actions import MemoryActionResolver
from otomekairo.memory.utils import build_memory_unit_semantic_text, source_text_hash
from otomekairo.store.file_store import FileStore


TIME = "2026-10-17T09:00:00+09:00"
MEMORY_SET = "memory_set:default"
EMBEDDING = {"model": "mock", "embedding_dimension": 2}


def candidate(*, object_value=None, **changes):
    return {
        "memory_type": "interpretation", "scope_type": "topic", "scope_key": "topic:test",
        "subject_ref": "self", "predicate": "understands", "object_ref_or_value": object_value,
        "summary_text": "検証用の理解。", "status": "inferred", "commitment_state": None,
        "confidence": 0.8, "salience": 0.8, "valid_from": None, "valid_to": None,
        "qualifiers": {"object_kind": "reference" if object_value is not None else "none"},
        "reason": "検証用の根拠。", **changes,
    }


@pytest.fixture
def context(tmp_path):
    store = FileStore(tmp_path)
    llm = Mock()
    llm.generate_embeddings.side_effect = lambda **kw: [[1.0, 0.0] for _ in kw["texts"]]
    return store, llm, MemoryActionResolver(store=store, llm=llm)


def resolve(resolver, candidates):
    return resolver.resolve_memory_actions_batch(
        memory_set_id=MEMORY_SET, finished_at=TIME, event_ids=[], cycle_ids=[],
        candidates=candidates, embedding_definition=EMBEDDING,
    )


def seed(store, *candidates):
    actions = resolve(MemoryActionResolver(store=store), list(candidates))
    store.persist_memory_actions(memory_actions=actions)
    return [action["memory_unit"] for action in actions]


@pytest.mark.parametrize("old,new", [
    (candidate(object_value="topic:known"), candidate(object_value="topic:known")),
    (candidate(), candidate()),
    (candidate(object_value="topic:old"), candidate(object_value="topic:new")),
    (candidate(object_value="topic:known"), candidate(qualifiers={"source": "explicit_statement"})),
    (candidate(object_value="topic:known", qualifiers={"source": "explicit_statement"}), candidate()),
    (candidate(object_value="topic:known"), candidate(qualifiers={"allow_parallel": True})),
    (candidate(object_value="topic:known"), candidate(qualifiers={"replace_prior": True})),
    (candidate(object_value="topic:known"), candidate(qualifiers={"negates_previous": True})),
    (candidate(object_value="topic:known"), candidate(qualifiers={"condition": "fixture"})),
    (candidate(object_value="topic:known"), candidate(commitment_state="done")),
    (candidate(object_value="topic:known"), candidate(status="revoked")),
    (candidate(object_value="topic:known"), candidate(status="dormant")),
    (candidate(object_value="topic:known"), candidate(status="superseded")),
    (candidate(object_value="topic:known"), candidate(confidence=0.1)),
])
def test_structurally_decided_updates_need_no_embeddings(context, old, new):
    store, llm, resolver = context
    seed(store, old)
    # 同じ正本へ既存の構造条件だけで適用した操作と一致する。
    expected = resolve(MemoryActionResolver(store=store), [new])
    actual = resolve(resolver, [new])
    llm.generate_embeddings.assert_not_called()
    assert [a["operation"] for a in actual] == [a["operation"] for a in expected]
    for result, original in zip(actual, expected, strict=True):
        assert result["memory_unit"]["summary_text"] == original["memory_unit"]["summary_text"]
        assert result["memory_unit"]["object_ref_or_value"] == original["memory_unit"]["object_ref_or_value"]
        assert result["memory_unit"]["status"] == original["memory_unit"]["status"]


@pytest.mark.parametrize("similarity,operation", [(0.89, "create"), (0.9, "refine"), (0.99, "refine")])
@pytest.mark.parametrize("known_side", ["existing", "candidate"])
def test_semantic_refine_precedes_implicit_parallel_and_preserves_known_object(
    context, similarity, operation, known_side,
):
    store, llm, resolver = context
    old = candidate(object_value="topic:known" if known_side == "existing" else None)
    new = candidate(object_value="topic:known" if known_side == "candidate" else None,
                    summary_text="補足された理解。")
    unit = seed(store, old)[0]
    new_text = build_memory_unit_semantic_text(new)
    llm.generate_embeddings.side_effect = lambda **kw: [
        [similarity, math.sqrt(1.0 - similarity ** 2)] if text == new_text else [1.0, 0.0]
        for text in kw["texts"]
    ]
    action, = resolve(resolver, [new])
    assert action["operation"] == operation
    if operation == "refine":
        assert action["memory_unit_id"] == unit["memory_unit_id"]
        assert action["memory_unit"]["object_ref_or_value"] == "topic:known"
        assert action["memory_unit"]["qualifiers"]["object_kind"] == "reference"
        assert action["memory_unit"]["summary_text"] == new["summary_text"]
        assert "_semantic_similarity" not in action["memory_unit"]
        assert "_semantic_similarity" not in action["before_snapshot"]
        store.persist_memory_actions(memory_actions=[action])
        persisted = store.list_memory_units_by_id(memory_set_id=MEMORY_SET, memory_unit_ids=[unit["memory_unit_id"]])[0]
        assert persisted["object_ref_or_value"] == "topic:known"
        assert "_semantic_similarity" not in persisted
    assert llm.generate_embeddings.call_count == 1


@pytest.mark.parametrize("count,batch_sizes", [(3, [6]), (33, [64, 2])])
def test_candidates_are_embedded_together_without_changing_order(context, count, batch_sizes):
    store, llm, resolver = context
    old = [candidate(object_value=f"topic:known:{i}", predicate=f"axis:{i}") for i in range(count)]
    units = seed(store, *old)
    new = [candidate(predicate=f"axis:{i}", summary_text=f"補足 {i}。") for i in range(count)]
    actions = resolve(resolver, new)
    assert [len(call.kwargs["texts"]) for call in llm.generate_embeddings.call_args_list] == batch_sizes
    assert [action["memory_unit_id"] for action in actions] == [unit["memory_unit_id"] for unit in units]
    assert [action["memory_unit"]["summary_text"] for action in actions] == [item["summary_text"] for item in new]
    assert all(action["operation"] == "refine" for action in actions)


def test_same_comparison_text_is_sent_once_per_batch_operation(context):
    store, llm, resolver = context
    seed(store, candidate(object_value="topic:known"))
    new = candidate()
    actions = resolve(resolver, [new, deepcopy(new)])
    assert len(actions) == 2
    assert llm.generate_embeddings.call_count == 1
    texts = llm.generate_embeddings.call_args.kwargs["texts"]
    assert len(texts) == len(set(texts)) == 2


@pytest.mark.parametrize("fresh", [True, False])
def test_only_matching_full_text_hash_reuses_existing_vector(context, monkeypatch, fresh):
    store, llm, resolver = context
    unit = seed(store, candidate(object_value="topic:known"))[0]
    text_hash = source_text_hash(build_memory_unit_semantic_text(unit)) if fresh else "stale"
    monkeypatch.setattr(store, "get_memory_unit_embeddings", Mock(return_value={
        unit["memory_unit_id"]: {"text_hash": text_hash, "embedding": [1.0, 0.0]},
    }))
    action, = resolve(resolver, [candidate()])
    assert action["operation"] == "refine"
    assert len(llm.generate_embeddings.call_args.kwargs["texts"]) == (1 if fresh else 2)


def test_semantic_match_with_highest_similarity_is_selected(context):
    store, llm, resolver = context
    units = seed(store, candidate(object_value="topic:first", summary_text="候補一。"),
                 candidate(object_value="topic:second", summary_text="候補二。"))
    vectors = {build_memory_unit_semantic_text(units[0]): [0.92, math.sqrt(1 - 0.92 ** 2)],
               build_memory_unit_semantic_text(units[1]): [1.0, 0.0]}
    llm.generate_embeddings.side_effect = lambda **kw: [vectors.get(text, [1.0, 0.0]) for text in kw["texts"]]
    action, = resolve(resolver, [candidate()])
    assert action["memory_unit_id"] == units[1]["memory_unit_id"]


def test_failed_batch_has_no_memory_side_effects(context):
    store, llm, resolver = context
    original = seed(store, candidate(object_value="topic:known"))[0]
    llm.generate_embeddings.side_effect = RuntimeError("fixture embedding failure")
    with pytest.raises(RuntimeError, match="fixture embedding failure"):
        resolve(resolver, [candidate(predicate="new_axis"), candidate()])
    units = store.find_memory_units_for_compare(
        memory_set_id=MEMORY_SET, memory_type="interpretation", scope_type="topic",
        scope_key="topic:test", subject_ref="self", predicate="understands",
    )
    assert units == [original]


def test_empty_batch_and_new_memory_need_no_comparison_request(context):
    _, llm, resolver = context
    assert resolve(resolver, []) == []
    assert resolve(resolver, [candidate()])[0]["operation"] == "create"
    llm.generate_embeddings.assert_not_called()


def test_reused_vector_is_bound_to_full_text_even_if_a_record_changes_between_reads(context, monkeypatch):
    store, llm, resolver = context
    old = {**candidate(object_value="topic:known"), "memory_unit_id": "memory_unit:test"}
    changed = {**old, "summary_text": "別のsnapshotの理解。"}
    old_text = build_memory_unit_semantic_text(old)
    changed_text = build_memory_unit_semantic_text(changed)
    monkeypatch.setattr(store, "get_memory_unit_embeddings", Mock(return_value={
        old["memory_unit_id"]: {"text_hash": source_text_hash(old_text), "embedding": [1.0, 0.0]},
    }))
    llm.generate_embeddings.side_effect = lambda **kw: [
        [0.0, 1.0] if text == changed_text else [1.0, 0.0] for text in kw["texts"]
    ]
    first, second = resolver._annotate_semantic_match_batches(
        comparisons=[(candidate(), [old]), (candidate(), [changed])],
        embedding_definition=EMBEDDING, memory_set_id=MEMORY_SET,
    )
    assert first[0]["_semantic_similarity"] == 1.0
    assert second[0]["_semantic_similarity"] == 0.0
    assert old_text not in llm.generate_embeddings.call_args.kwargs["texts"]
    assert changed_text in llm.generate_embeddings.call_args.kwargs["texts"]
