from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from otomekairo.llm.contracts import LLMError
from otomekairo.llm.client import LLMClient
from otomekairo.llm.contexts import PersonaContext
from otomekairo.defaults import build_default_state
from otomekairo.memory.consolidator import MemoryConsolidator
from otomekairo.memory.reflection.evidence import build_summary_source_pack
from otomekairo.memory.reflection.summary import MemoryReflectionSummaryMixin
from otomekairo.store.file_store import FileStore


def memory_item(reflection, *, event_ids, subject_ref="person:target"):
    return reflection._summary_pack_memory_item({
        "memory_type": "interpretation", "scope_type": "entity", "scope_key": "person:target",
        "subject_ref": subject_ref, "predicate": "recurring_pattern", "summary_text": "記憶の要点。",
        "evidence_event_ids": event_ids,
        "qualifiers": {"source": "interpretation", "conditions": {"when": "相談時", "participant": "person:other"}},
    })


def prepared(reflection, scope_ref, event_ids):
    return {
        "scope_ref": scope_ref, "scope_type": "entity", "scope_key": f"person:{scope_ref}",
        "evidence_pack": {
            "scope_ref": scope_ref, "episodes": [],
            "memory_units": [memory_item(reflection, event_ids=event_ids)],
        },
    }


def generate(reflection, scopes):
    reflection.llm = Mock()
    reflection.llm.review_memory_reflection_summaries.side_effect = lambda **kw: {"reviews": [
        {"scope_ref": row["scope_ref"], "scope_supported": True, "supported": True, "reason_summary": "照合済み。"}
        for row in kw["source_pack"]["summary_candidates"]
    ]}
    reflection.llm.generate_memory_reflection_summary.side_effect = lambda **kwargs: {"summaries": [
        {"scope_ref": item["scope_ref"], "summary_text": "検証用の要約。"}
        for item in kwargs["source_pack"]["scopes"]
    ]}
    trace = reflection._empty_summary_generation()
    texts = reflection._generate_reflective_summary_texts(
        memory_set_id="memory_set:test", prepared_scopes=scopes, persona_context=object(),
        reflection_summary_model_config={"model": "mock"}, summary_generation=trace,
    )
    return texts, trace


def test_source_pack_keeps_subject_speaker_and_shared_primary_evidence_separate():
    reflection = MemoryReflectionSummaryMixin()
    episode = reflection._summary_pack_episode_item({
        "episode_type": "conversation", "primary_scope_type": "entity", "primary_scope_key": "person:target",
        "source_interaction_refs": ["interaction:test"], "source_participant_refs": ["person:target", "person:other"],
        "linked_event_ids": ["event:statement", "event:reply"], "summary_text": "相談をした。",
    })
    packs = [{
        "scope_ref": "scope:0", "episodes": [episode],
        "memory_units": [memory_item(reflection, event_ids=["event:statement", "event:reply"])],
    }, {
        "scope_ref": "scope:1", "episodes": [],
        "memory_units": [memory_item(reflection, event_ids=["event:reply"], subject_ref="self")],
    }]
    original = deepcopy(packs)
    long_reply = "話者と対象の対応を保持する検証用本文。" * 300
    events = {
        "event:statement": {
            "event_id": "event:statement", "cycle_id": "cycle:test", "kind": "conversation_input", "role": "user",
            "speaker_ref": "person:other", "participant_refs": ["person:target", "person:other"],
            "interaction_ref": "interaction:test", "created_at": "2026-10-25T00:00:03Z",
            "text": "相手について説明する。", "unused_payload": {"value": "渡さない項目。"},
        },
        "event:reply": {
            "event_id": "event:reply", "cycle_id": "cycle:test", "kind": "speech", "role": "assistant",
            "speaker_ref": "self", "participant_refs": ["person:target", "person:other"],
            "interaction_ref": "interaction:test", "created_at": "2026-10-25T00:00:04Z", "text": long_reply,
        },
    }
    pack = build_summary_source_pack(evidence_packs=packs, events_by_id=events)
    assert packs == original
    assert len(pack["source_events"]) == 2
    first, second = pack["scopes"]
    memory = first["memory_units"][0]
    assert memory["subject_ref"] == "person:target"
    assert memory["scope_key"] == "person:target"
    assert memory["qualifiers"] == original[0]["memory_units"][0]["qualifiers"]
    assert memory["source_event_refs"] == first["episodes"][0]["source_event_refs"]
    statement_ref, reply_ref = memory["source_event_refs"]
    assert pack["source_events"][statement_ref]["speaker_ref"] == "person:other"
    assert pack["source_events"][statement_ref]["role"] == "user"
    assert pack["source_events"][reply_ref]["speaker_ref"] == "self"
    assert pack["source_events"][reply_ref]["text"] == long_reply
    assert pack["source_events"][statement_ref]["formed_time_label"] == "2026年10月25日 9時00分03秒"
    assert second["memory_units"][0]["source_event_refs"] == [reply_ref]
    assert first["episodes"][0]["source_participant_refs"] == ["person:target", "person:other"]
    for source in pack["source_events"].values():
        assert "event_id" not in source
        assert "cycle_id" not in source
        assert "created_at" not in source
        assert "unused_payload" not in source
    assert "source_event_ids" not in memory
    memory["qualifiers"]["conditions"]["when"] = "別の条件"
    assert packs == original


def test_missing_evidence_only_blocks_affected_scope(tmp_path: Path):
    reflection = MemoryReflectionSummaryMixin()
    reflection.store = FileStore(tmp_path)
    reflection.store.append_events(events=[{
        "event_id": "event:present", "cycle_id": "cycle:test", "memory_set_id": "memory_set:test",
        "kind": "speech", "role": "assistant", "speaker_ref": "self", "text": "自分の方針。",
        "created_at": "2026-10-25T00:00:00Z",
    }])
    texts, trace = generate(reflection, [
        prepared(reflection, "scope:0", ["event:missing"]),
        prepared(reflection, "scope:1", ["event:present"]),
    ])
    assert set(texts) == {"scope:1"}
    assert trace["llm_call_count"] == 2
    assert trace["failed_scopes"] == [{
        "scope_type": "entity", "scope_key": "person:scope:0", "failure_stage": "load_source_events",
        "failure_reason": "内省要約の根拠イベントを取得できません。",
    }]
    source = reflection.llm.generate_memory_reflection_summary.call_args.kwargs["source_pack"]
    assert [scope["scope_ref"] for scope in source["scopes"]] == ["scope:1"]
    assert len(source["source_events"]) == 1
    reflection.llm.generate_memory_reflection_summary.assert_called_once()


def test_evidence_in_another_memory_set_is_not_used(tmp_path: Path):
    reflection = MemoryReflectionSummaryMixin()
    reflection.store = FileStore(tmp_path)
    reflection.store.append_events(events=[{
        "event_id": "event:other", "cycle_id": "cycle:other", "memory_set_id": "memory_set:other",
        "kind": "conversation_input", "role": "user", "speaker_ref": "person:other", "text": "別の記憶セット。",
        "created_at": "2026-10-25T00:00:00Z",
    }])
    texts, trace = generate(reflection, [prepared(reflection, "scope:0", ["event:other"])])
    assert texts == {}
    assert trace["llm_call_count"] == 0
    assert trace["failed_scopes"][0]["failure_stage"] == "load_source_events"
    reflection.llm.generate_memory_reflection_summary.assert_not_called()


def test_source_read_failure_is_explicit_and_never_calls_llm():
    reflection = MemoryReflectionSummaryMixin()
    reflection.store = Mock()
    reflection.store.load_events_for_evidence.side_effect = RuntimeError("source read failed")
    texts, trace = generate(reflection, [prepared(reflection, "scope:0", ["event:source"])])
    assert texts == {}
    assert trace["llm_call_count"] == 0
    assert len(trace["failed_scopes"]) == 1
    assert trace["failed_scopes"][0]["failure_stage"] == "load_source_events"
    reflection.llm.generate_memory_reflection_summary.assert_not_called()


def test_items_without_source_refs_keep_their_unknown_origin_explicit():
    reflection = MemoryReflectionSummaryMixin()
    item = memory_item(reflection, event_ids=[])
    pack = build_summary_source_pack(evidence_packs=[{
        "scope_ref": "scope:0", "episodes": [], "memory_units": [item],
    }], events_by_id={})
    assert pack["source_events"] == {}
    memory = pack["scopes"][0]["memory_units"][0]
    assert memory["source_event_refs"] == []
    assert "speaker_ref" not in memory
    assert memory["qualifiers"]["source"] == "interpretation"


@pytest.mark.parametrize("event_ids", [None, "event:wrong", [None], [[]], [""]])
def test_malformed_source_refs_fail_in_evidence_pack_building(event_ids):
    with pytest.raises(ValueError, match="根拠イベント参照"):
        memory_item(MemoryReflectionSummaryMixin(), event_ids=event_ids)


def test_invalid_model_snapshot_reports_intended_error():
    with pytest.raises(LLMError, match="model preset snapshot"):
        MemoryReflectionSummaryMixin()._reflection_summary_model_config(state={
            "selected_model_preset_id": "model:test", "model_presets": {"model:test": None},
        })


@pytest.mark.parametrize("assessment", [
    None, {}, {"scope_supported": True, "supported": False, "reason_summary": "帰属を確認できない。"},
    {"scope_supported": True, "supported": 1, "reason_summary": "確認した。"},
    {"scope_supported": True, "supported": "true", "reason_summary": "確認した。"},
    {"scope_supported": True, "supported": True, "reason_summary": ""},
    {"scope_supported": True, "supported": True, "reason_summary": "確認した。", "extra": True},
    {"scope_supported": False, "supported": True, "reason_summary": "本文は事実だが対象との対応がない。"},
    {"scope_supported": 1, "supported": True, "reason_summary": "確認した。"},
    {"supported": True, "reason_summary": "対象との対応判定が欠けている。"},
])
def test_failed_attribution_review_blocks_only_its_scope_in_one_review_call(assessment):
    reflection = MemoryReflectionSummaryMixin()
    reflection.llm = Mock()
    reflection.llm.generate_memory_reflection_summary.return_value = {"summaries": [
        {"scope_ref": "scope:0", "summary_text": "保存されない本文。"},
        {"scope_ref": "scope:1",
         "summary_text": "正常な本文。"},
    ]}
    reflection.llm.review_memory_reflection_summaries.return_value = {"reviews": [
        {"scope_ref": "scope:0", **assessment} if isinstance(assessment, dict) else assessment,
        {"scope_ref": "scope:1", "scope_supported": True, "supported": True, "reason_summary": "照合済み。"},
    ]}
    trace = reflection._empty_summary_generation()
    texts = reflection._generate_reflective_summary_texts(
        memory_set_id="memory_set:test", prepared_scopes=[
            prepared(reflection, "scope:0", []), prepared(reflection, "scope:1", []),
        ], persona_context=object(), reflection_summary_model_config={"model": "mock"}, summary_generation=trace,
    )
    assert texts == {"scope:1": "正常な本文。"}
    assert len(trace["failed_scopes"]) == 1
    assert trace["failed_scopes"][0]["scope_key"] == "person:scope:0"
    assert trace["failed_scopes"][0]["failure_stage"] == "verify_summary_attribution"
    assert trace["llm_call_count"] == 2
    reflection.llm.generate_memory_reflection_summary.assert_called_once()


def test_saved_summary_evidence_matches_selected_input_without_twelve_event_cutoff(tmp_path: Path):
    store = FileStore(tmp_path)
    reflection = MemoryConsolidator(store=store, llm=Mock()).reflective
    scope = {"scope_type": "entity", "scope_key": "person:test", "subject_ref": "person:test"}
    seeds = []
    units = []
    expected_event_ids = []
    for i in range(10):
        event_ids = [f"event:{i}:{j}" for j in range(20 if i == 0 else 1)]
        if i < 8:
            expected_event_ids.extend(event_ids)
        store.append_events(events=[{
            "event_id": eid, "cycle_id": f"cycle:{i}", "memory_set_id": "memory_set:default",
            "kind": "conversation_input", "role": "user", "speaker_ref": "person:test",
            "text": "テスト用の発言。", "created_at": "2026-10-25T00:00:00Z",
        } for eid in event_ids])
        candidate = {
            **scope, "memory_type": "preference", "predicate": f"pattern_{i}", "object_ref_or_value": None,
            "summary_text": "現在有効な根拠。", "status": "inferred", "commitment_state": None,
            "confidence": 0.8, "salience": 0.9 - i * 0.05,
            "valid_from": None, "valid_to": None, "qualifiers": {}, "reason": "テスト用の記憶。",
        }
        actions = reflection.action_resolver.resolve_memory_actions(
            memory_set_id="memory_set:default", finished_at="2026-10-25T00:00:00Z",
            event_ids=event_ids, cycle_ids=[f"cycle:{i}"], candidate=candidate,
        )
        store.persist_memory_actions(memory_actions=actions)
        seeds.extend(actions)
        units.append(actions[0]["memory_unit"])
    reflection.llm.generate_memory_reflection_summary.return_value = {"summaries": [
        {"scope_ref": "scope:0", "summary_text": "今回の根拠から作成した要約。"},
    ]}
    reflection.llm.review_memory_reflection_summaries.side_effect = lambda **kw: {"reviews": [
        {"scope_ref": row["scope_ref"], "scope_supported": True, "supported": True, "reason_summary": "照合済み。"}
        for row in kw["source_pack"]["summary_candidates"]
    ]}
    state = build_default_state()
    actions, trace = reflection._build_reflective_summary_actions(
        memory_set_id="memory_set:default", finished_at="2026-10-25T00:01:00Z", episodes=[],
        active_units=units, embedding_definition={"model": "mock", "embedding_dimension": 8},
        reflection_summary_model_config={"model": "mock"},
        selected_persona=state["personas"][state["selected_persona_id"]],
        scope_support_index={}, memory_actions=seeds,
    )
    assert trace["succeeded_scope_count"] == 1
    source = reflection.llm.generate_memory_reflection_summary.call_args.kwargs["source_pack"]
    assert "existing_summary_text" not in source["scopes"][0]
    assert len(source["scopes"][0]["memory_units"]) == 8
    assert len(source["source_events"]) == len(expected_event_ids)
    assert actions[0]["memory_unit"]["evidence_event_ids"] == expected_event_ids
    assert actions[0]["related_memory_unit_ids"] == [unit["memory_unit_id"] for unit in units[:8]]
    store.persist_memory_actions(memory_actions=actions)
    stored = store.list_memory_units_by_id(
        memory_set_id="memory_set:default", memory_unit_ids=[actions[0]["memory_unit_id"]],
    )
    assert stored[0]["evidence_event_ids"] == expected_event_ids
    before = deepcopy(stored[0])
    reflection.llm.review_memory_reflection_summaries.side_effect = None
    reflection.llm.review_memory_reflection_summaries.return_value = {"reviews": [
        {"scope_ref": "scope:0", "scope_supported": True, "supported": False, "reason_summary": "帰属を確認できない。"},
    ]}
    blocked, blocked_trace = reflection._build_reflective_summary_actions(
        memory_set_id="memory_set:default", finished_at="2026-10-25T00:02:00Z", episodes=[],
        active_units=units + stored, embedding_definition={"model": "mock", "embedding_dimension": 8},
        reflection_summary_model_config={"model": "mock"},
        selected_persona=state["personas"][state["selected_persona_id"]],
        scope_support_index={}, memory_actions=seeds,
    )
    assert blocked == []
    assert blocked_trace["failed_scopes"][0]["failure_stage"] == "verify_summary_attribution"
    assert store.list_memory_units_by_id(
        memory_set_id="memory_set:default", memory_unit_ids=[before["memory_unit_id"]],
    ) == [before]


@pytest.mark.parametrize("reviews", [
    [{"scope_ref": "scope:0", "scope_supported": True, "supported": True, "reason_summary": "一件目。"},
     {"scope_ref": "scope:0", "scope_supported": True, "supported": True, "reason_summary": "二件目。"}],
    [],
    [{"scope_ref": "scope:0", "scope_supported": True, "supported": True, "reason_summary": "確認。", "summary_text": "書き換えた本文。"}],
])
def test_review_duplicate_missing_or_rewrite_only_blocks_affected_scope(reviews):
    reflection = MemoryReflectionSummaryMixin()
    reflection.llm = Mock()
    reflection.llm.generate_memory_reflection_summary.return_value = {"summaries": [
        {"scope_ref": f"scope:{i}", "summary_text": f"元の本文{i}。"} for i in range(2)
    ]}
    reflection.llm.review_memory_reflection_summaries.return_value = {"reviews": reviews + [
        {"scope_ref": "scope:1", "scope_supported": True, "supported": True, "reason_summary": "確認。"},
    ]}
    trace = reflection._empty_summary_generation()
    texts = reflection._generate_reflective_summary_texts(
        memory_set_id="memory_set:test", prepared_scopes=[
            prepared(reflection, "scope:0", []), prepared(reflection, "scope:1", []),
        ], persona_context=object(), reflection_summary_model_config={"model": "mock"}, summary_generation=trace,
    )
    assert texts == {"scope:1": "元の本文1。"}
    assert len(trace["failed_scopes"]) == 1
    assert trace["failed_scopes"][0]["failure_stage"] == "verify_summary_attribution"
    assert trace["llm_call_count"] == 2


def test_review_provider_failure_is_not_treated_as_an_approval():
    reflection = MemoryReflectionSummaryMixin()
    reflection.llm = Mock()
    reflection.llm.generate_memory_reflection_summary.return_value = {"summaries": [
        {"scope_ref": "scope:0", "summary_text": "照合前の本文。"},
    ]}
    reflection.llm.review_memory_reflection_summaries.side_effect = RuntimeError("review unavailable")
    trace = reflection._empty_summary_generation()
    texts = reflection._generate_reflective_summary_texts(
        memory_set_id="memory_set:test", prepared_scopes=[prepared(reflection, "scope:0", [])],
        persona_context=object(), reflection_summary_model_config={"model": "mock"}, summary_generation=trace,
    )
    assert texts == {}
    assert len(trace["failed_scopes"]) == 1
    assert trace["failed_scopes"][0]["failure_stage"] == "verify_summary_attribution"
    assert trace["llm_call_count"] == 2


def test_review_client_keeps_item_rejection_for_the_caller_without_provider_repair(monkeypatch):
    payload = {"reviews": [{
        "scope_ref": "scope:0", "supported": False, "scope_supported": False,
        "reason_summary": "本文と対象の対応が成立しない。",
    }]}
    completion = Mock(return_value=json.dumps(payload))
    monkeypatch.setattr("otomekairo.llm.client.complete_text", completion)
    result = LLMClient().review_memory_reflection_summaries(
        model_config={"model": "provider/test"},
        persona_context=PersonaContext("Test", "テスト人格。", None, "判断の基底。"),
        source_pack={"scopes": [], "source_events": {}, "summary_candidates": [
            {"scope_ref": "scope:0", "summary_text": "照合対象。"},
        ]},
    )
    assert result == payload
    completion.assert_called_once()
