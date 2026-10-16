from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock
import json

import pytest

from otomekairo.defaults import build_default_state
from otomekairo.llm.client import LLMError
from otomekairo.llm.contexts import CurrentInput, build_persona_context
from otomekairo.llm.prompts import build_input_interpretation_messages
from otomekairo.recall.builder import RecallBuilder
from otomekairo.service.input.mixin import ServiceInputMixin
from otomekairo.store.file_store import FileStore


TIME = "2026-10-16T09:00:00+09:00"
MEMORY_SET = "memory_set:default"
HINT = {"primary_recall_focus": "episodic", "secondary_recall_focuses": [], "confidence": 0.8,
        "time_reference": "past", "focus_scopes": [], "mentioned_entities": [],
        "mentioned_topics": [], "risk_flags": []}


def record(key, text, **changes):
    return {"visual_observation_id": key, "memory_set_id": MEMORY_SET, "cycle_id": "cycle:old",
            "observed_at": "2026-09-01T09:00:00+09:00", "source_kind": "desktop",
            "source_label": "画面", "vision_source_id": "vision_source:desktop",
            "image_input_kind": "vision_capture_result", "confidence_hint": "medium",
            "retention_status": "active", "detailed_summary_text": text, "importance_score": 0.2,
            **changes}


def put(store, *records):
    with store._memory_db() as conn:
        for item in records:
            store._insert_visual_observation_record(conn, item)


@pytest.fixture
def setup(tmp_path):
    store = FileStore(tmp_path)
    state = build_default_state()
    state["memory_sets"][MEMORY_SET]["embedding"]["embedding_dimension"] = 2
    vectors = {}
    embeddings = Mock(side_effect=lambda **kw: [vectors.get(text, [8.0, 8.0]) for text in kw["texts"]])
    builder = RecallBuilder(store=store, llm=SimpleNamespace(generate_embeddings=embeddings))
    return store, state, vectors, embeddings, builder


def sync(builder, state, cycle="cycle:now"):
    return builder._sync_visual_observation_index(state=state, cycle_id=cycle)


def search(store, memory_set=MEMORY_SET, cycle="cycle:now", limit=1):
    return store.search_visual_observation_vectors(memory_set_id=memory_set, cycle_id=cycle,
             query_embedding=[0.0, 0.0], embedding_dimension=2, limit=limit)


def test_visual_semantic_recall_finds_old_experience_using_each_current_observation(setup):
    store, state, vectors, embeddings, builder = setup
    old = record("visual:old", "取っ手のある赤い陶器を机の端で見た。", retention_status="compressed")
    put(store, old, *(record(f"visual:new:{i}", f"別の作業の記録 {i}",
                            observed_at=TIME) for i in range(20)))
    now = "机に置かれた深紅のマグカップ。" * 30
    vectors[old["detailed_summary_text"]] = [0.0, 0.0]
    vectors[now] = [0.0, 0.0]
    observations = [{"visual_observation_id": "visual:current", "visual_summary_text": now}]
    pack = builder.build_recall_pack(state=state, cycle_id="cycle:now", current_time=TIME,
             current_visual_observations=observations, augmented_query_text="いまの様子", recall_hint=HINT,
             current_person_ref=None)
    assert pack["visual_observations"][0]["visual_observation_id"] == "visual:old"
    assert pack["visual_observations"][0]["detailed_summary_text"] == old["detailed_summary_text"]
    assert len(pack["visual_observations"]) <= 8
    assert now in embeddings.call_args.kwargs["texts"]
    assert pack["visual_recall"]["index_sync"]["indexed_count"] == 21
    assert "visual:old" in pack["visual_recall"]["selected_ids"]


def test_visual_index_updates_only_changed_or_missing_vectors(setup):
    store, state, vectors, embeddings, builder = setup
    first, second = record("visual:1", "以前の説明"), record("visual:2", "継続する説明")
    put(store, first, second)
    assert sync(builder, state)["indexed_count"] == 2
    embeddings.reset_mock()
    assert sync(builder, state)["indexed_count"] == 0
    embeddings.assert_not_called()
    put(store, {**first, "detailed_summary_text": "更新した説明"})
    assert sync(builder, state)["indexed_count"] == 1
    assert embeddings.call_args.kwargs["texts"] == ["更新した説明"]
    with store._memory_db() as conn:
        conn.execute("DELETE FROM visual_observation_vec WHERE id IN "
                     "(SELECT vector_entry_id FROM visual_observation_vector_entries WHERE visual_observation_id = ?)",
                     (second["visual_observation_id"],))
    assert sync(builder, state)["indexed_count"] == 1
    assert embeddings.call_args.kwargs["texts"] == ["継続する説明"]


def test_knn_filters_memory_set_excluded_and_current_cycle_before_limit(setup):
    store, state, vectors, embeddings, builder = setup
    records = [record("visual:valid", "有効な過去"), record("visual:excluded", "検索除外"),
               record("visual:current", "今回の記録"),
               record("visual:foreign", "別集合", memory_set_id="memory_set:other")]
    vectors.update({item["detailed_summary_text"]: [0.0, 0.0] for item in records})
    vectors["有効な過去"] = [0.2, 0.2]
    put(store, *records)
    sync(builder, state)
    other = deepcopy(state)
    other["memory_sets"]["memory_set:other"] = deepcopy(state["memory_sets"][MEMORY_SET])
    other["selected_memory_set_id"] = "memory_set:other"
    sync(builder, other)
    put(store, {**records[1], "retention_status": "excluded"}, {**records[2], "cycle_id": "cycle:now"})
    assert search(store)[0]["record"]["visual_observation_id"] == "visual:valid"
    selected = builder._build_visual_observations(memory_set_id=MEMORY_SET, cycle_id="cycle:now",
                                                 queried_records=[search(store)[0]["record"]], limit=8)
    assert {item["visual_observation_id"] for item in selected} == {"visual:valid"}


def test_index_reset_clone_and_delete_regenerate_from_visual_records(setup):
    store, state, vectors, embeddings, builder = setup
    put(store, record("visual:1", "赤い器"))
    sync(builder, state)
    store.reset_memory_set_vector_index(MEMORY_SET)
    assert sync(builder, state)["indexed_count"] == 1
    store.clone_memory_set_records(source_memory_set_id=MEMORY_SET, target_memory_set_id="memory_set:copy")
    copied = deepcopy(state)
    copied["memory_sets"]["memory_set:copy"] = deepcopy(state["memory_sets"][MEMORY_SET])
    copied["selected_memory_set_id"] = "memory_set:copy"
    assert sync(builder, copied)["indexed_count"] == 1
    hit = search(store, memory_set="memory_set:copy")[0]["record"]
    assert hit["memory_set_id"] == "memory_set:copy"
    assert hit["visual_observation_id"] != "visual:1"
    store.delete_memory_set_records("memory_set:copy")
    assert search(store, memory_set="memory_set:copy") == []
    assert len(search(store)) == 1


def test_visual_embedding_failure_fails_recall_instead_of_using_text_search(setup):
    store, state, vectors, embeddings, builder = setup
    put(store, record("visual:1", "保存済みの記録"))
    embeddings.side_effect = LLMError("fixture provider failure")
    with pytest.raises(LLMError, match="意味検索索引"):
        builder.build_recall_pack(state=state, cycle_id="cycle:now", current_time=TIME,
               current_visual_observations=[], augmented_query_text="同じもの？", recall_hint=HINT,
               current_person_ref=None)
    with store._memory_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM visual_observation_vector_entries").fetchone()[0] == 0


def observation(key, source="conversation_attachment", **changes):
    return {"visual_observation_id": key, "source": source, "image_input_kind": source,
            "image_interpreted": True, "visual_summary_text": "形や色の詳しい説明。" * 70,
            "visual_confidence_hint": "medium", **changes}


@pytest.mark.parametrize("trigger,source", [("user_message", "conversation_attachment"),
    ("capability_result", "capability_result"), ("wake", "wake_reference")])
def test_current_observation_collection_preserves_details_sources_and_original_input(trigger, source):
    service = ServiceInputMixin()
    direct = observation("visual:direct", source, capability_id="vision.capture")
    wake = observation("visual:wake", "capability_result", status="succeeded", capability_id="vision.capture",
                       source_kind="camera", vision_source_id="vision_source:camera",
                       image_input_kind="vision_capture_result")
    context = {"wake_observations": [wake, wake, {**wake, "visual_observation_id": "failed", "status": "failed"}]}
    result = service._build_current_visual_observations(trigger_kind=trigger, observation_summary=direct,
                 client_context=context, observed_at=TIME)
    assert len(result) == 2
    assert result[0]["visual_summary_text"] == direct["visual_summary_text"]
    assert result[1]["source"] == "wake_observation"
    assert result[1]["source_owner"] == "self"
    assert all(item["observed_at"] == TIME for item in result)
    text = "この絵を見てどう思う？"
    query = service._pipeline_augmented_query_text(input_text=text, trigger_kind=trigger,
                  observation_summary=direct, current_visual_observations=result)
    assert text == "この絵を見てどう思う？"
    assert all(item["visual_summary_text"] in query for item in result)
    persona = build_persona_context({"persona_prompt": "観測を根拠に考える。"}, role="input_interpretation")
    current_input = CurrentInput(sender_kind="person", sender_ref="person:test", source_kind="user_message",
                                response_target_refs=("person:test",), interaction_context=None, text=text)
    messages = build_input_interpretation_messages(persona_context=persona, current_input=current_input,
                   recent_turns=[], current_time=TIME, current_visual_observations=result)
    internal = json.loads(messages[1]["content"].split("\n")[1])
    assert internal["current_visual_observations"] == result
    assert direct["visual_summary_text"] not in messages[2]["content"]
    assert text in messages[2]["content"]


def test_no_current_images_preserves_query_and_empty_observations():
    service = ServiceInputMixin()
    assert service._build_current_visual_observations(trigger_kind="user_message", observation_summary=None,
               client_context={}, observed_at=TIME) == []
    assert service._pipeline_augmented_query_text(input_text="元の発話", trigger_kind="user_message",
               observation_summary=None, current_visual_observations=[]) == "元の発話"


def test_multiple_current_observations_are_searched_in_one_query_batch(setup):
    store, state, vectors, embeddings, builder = setup
    put(store, record("visual:cup", "丸い青い器。"),
        record("visual:painting", "緑の山の風景。", vision_source_id="vision_source:camera", source_kind="camera"))
    vectors.update({"丸い青い器。": [0.0, 0.0], "緑の山の風景。": [2.0, 2.0],
                    "紺色のマグが見える。": [0.0, 0.0], "森と峰の絵がある。": [2.0, 2.0]})
    current = [{"visual_summary_text": text} for text in ("紺色のマグが見える。", "森と峰の絵がある。")]
    pack = builder.build_recall_pack(state=state, cycle_id="cycle:now", current_time=TIME,
               current_visual_observations=current, augmented_query_text="いまの様子", recall_hint=HINT,
               current_person_ref=None)
    query_batch = embeddings.call_args.kwargs["texts"]
    assert all(query_batch.count(item["visual_summary_text"]) == 1 for item in current)
    assert {item["visual_observation_id"] for item in pack["visual_observations"]} == {"visual:cup", "visual:painting"}


def test_visual_index_write_failure_rolls_back_metadata(setup):
    store, state, vectors, embeddings, builder = setup
    put(store, record("visual:1", "有効な詳細説明。"))
    embeddings.side_effect = lambda **kw: [[0.0] for text in kw["texts"]]
    with pytest.raises(LLMError, match="意味検索索引"):
        sync(builder, state)
    with store._memory_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM visual_observation_vector_entries").fetchone()[0] == 0


def test_visual_search_failure_propagates_as_recall_failure(setup, monkeypatch):
    import sqlite3
    store, state, vectors, embeddings, builder = setup
    put(store, record("visual:1", "過去の視覚記録。"))
    monkeypatch.setattr(store, "search_visual_observation_vectors", Mock(side_effect=sqlite3.OperationalError("fixture search failure")))
    with pytest.raises(LLMError, match="意味検索に失敗"):
        builder.build_recall_pack(state=state, cycle_id="cycle:now", current_time=TIME,
               current_visual_observations=[], augmented_query_text="以前見た？", recall_hint=HINT,
               current_person_ref=None)


def test_no_visual_history_is_a_successful_empty_search(setup):
    store, state, vectors, embeddings, builder = setup
    pack = builder.build_recall_pack(state=state, cycle_id="cycle:now", current_time=TIME,
               current_visual_observations=[], augmented_query_text="こんにちは", recall_hint=HINT,
               current_person_ref=None)
    assert pack["visual_observations"] == []
    assert pack["visual_recall"]["index_sync"] == {"source_count": 0, "indexed_count": 0}


def test_decision_input_keeps_current_observations_and_historical_details_separate(setup):
    from dataclasses import fields, MISSING
    from otomekairo.llm.contexts import DecisionContext
    from otomekairo.llm.prompts import build_decision_messages
    store, state, vectors, embeddings, builder = setup
    current = observation("visual:now", source_kind="camera")
    prior = builder._to_visual_observation_item(record("visual:old", "以前の細部の記録。" * 80))
    payload = {field.name: None for field in fields(DecisionContext) if field.default is MISSING and field.default_factory is MISSING}
    payload.update(input_text="前にも見た？", current_input=CurrentInput(
          sender_kind="person", sender_ref="person:test", source_kind="user_message",
          response_target_refs=("person:test",), interaction_context=None, text="前にも見た？"),
          trigger_kind="user_message", recent_turns=[], time_context={}, affect_context={},
          current_visual_observations=[current], recall_hint=HINT, recall_pack={"visual_observations": [prior]})
    persona = build_persona_context({"persona_prompt": "根拠を確かめて答える。"}, role="decision_generation")
    messages = build_decision_messages(persona_context=persona, context=DecisionContext(**payload))
    payloads = [json.loads(message["content"].split("\n")[1]) for message in messages[1:]]
    internal = next(payload["internal_context"] for payload in payloads
                    if isinstance(payload, dict) and "internal_context" in payload)
    assert internal["current_visual_observations"] == [current]
    assert internal["recall_pack"]["visual_observations"][0]["detailed_summary_text"] == prior["detailed_summary_text"]
    assert current["visual_summary_text"] not in messages[-1]["content"]
