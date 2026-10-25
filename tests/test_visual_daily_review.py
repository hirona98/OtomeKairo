from copy import deepcopy
import json
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from otomekairo.llm.client import LLMClient
from otomekairo.llm.contracts import LLMError
from otomekairo.llm.contexts import build_persona_context
from otomekairo.llm.visual_daily import validate_grouping, validate_support
from otomekairo.memory.actions import MemoryActionResolver
from otomekairo.memory.vector import MemoryVectorIndexer
from otomekairo.service.app import OtomeKairoService
from otomekairo.service.visual_daily import ServiceVisualDailyMixin
from otomekairo.store.file_store import FileStore


RECORDS = [{"visual_observation_id": f"visual:{i}", "source_key": "vision:test"} for i in range(3)]
INTERLEAVED_RECORDS = [
    {"visual_observation_id": f"visual:{i}", "source_key": f"vision:{i % 2}"}
    for i in range(6)
]


def group(refs):
    return {"observation_ids": refs, "summary_text": "観測の要約。", "reason_summary": "根拠の説明。"}


def support_decision(*, indexes=None, status="supported", relation="new", refs=None,
                     shared="複数日の観測でPC机に作業者が見える。", refined=None):
    supported = status == "supported"
    indexes = [0] if indexes is None else indexes
    refs = ["past:0"] if refs is None else refs
    if refined is not None:
        shared = refined
    comparison = None
    if supported and relation in {"same", "refine", "parallel", "conflict"}:
        comparison = {
            "existing_claims": [{"claim_text": "既存の主張。", "entailed_by_shared": relation in {"same", "refine"},
                                 "unconfirmed_existing_parts": [] if relation in {"same", "refine"} else ["共通主張だけでは確認できない内容。"]}],
            "shared_claims": [{"claim_text": "共通の主張。", "entailed_by_existing": relation in {"same", "parallel"},
                               "unconfirmed_shared_parts": [] if relation in {"same", "parallel"} else ["旧記憶だけでは確認できない内容。"]}],
            "existing_conditions": "比較する対象・時点・条件。", "shared_conditions": "比較する対象・時点・条件。",
            "same_conditions_confirmed": True, "incompatible": relation == "conflict",
        }
    return {
        "source_claims": [{"group_ref": ref, "claim_text": shared}
                              for ref in [*(f"current:{index}" for index in indexes), *refs]] if supported else [],
        "candidate_indexes": indexes,
        "support_status": status, "shared_summary_text": shared if supported else None,
        "past_support_refs": refs if supported else [],
        "relation": relation if supported else "uncertain",
        "target_memory_ref": "memory:0" if supported and relation in {"same", "refine", "parallel", "conflict"} else None,
        "memory_comparison": comparison,
        "reason_summary": "観測根拠と正本の関係を審査した。",
    }


def test_grouping_requires_complete_ordered_evidence_and_same_source():
    validate_grouping({"groups": [group(["visual:0", "visual:1"]), group(["visual:2"])]}, RECORDS)
    for refs in (["visual:1", "visual:0", "visual:2"], ["visual:0", "visual:2"],
                 ["visual:0", "visual:0", "visual:2"], ["visual:unknown"]):
        with pytest.raises(LLMError):
            validate_grouping({"groups": [group(refs)]}, RECORDS)
    changed = deepcopy(RECORDS)
    changed[1]["source_key"] = "vision:other"
    with pytest.raises(LLMError):
        validate_grouping({"groups": [group(["visual:0", "visual:1", "visual:2"])]}, changed)


@pytest.mark.parametrize("groups", [
    [[0, 2, 4], [1, 3, 5]],
    [[1, 3, 5], [0, 2, 4]],
    [[0, 2], [1, 3, 5], [4]],
])
def test_grouping_preserves_order_within_each_interleaved_source(groups):
    validate_grouping({"groups": [group([f"visual:{i}" for i in refs]) for refs in groups]},
                      INTERLEAVED_RECORDS)


@pytest.mark.parametrize("groups", [
    [[0, 2], [1, 3, 5]],  # Missing evidence.
    [[0, 2, 4], [1, 3, 5], [0]],  # Evidence repeated across groups.
    [[2, 0, 4], [1, 3, 5]],  # Reversed observations in a group.
    [[4], [0, 2], [1, 3, 5]],  # Reversed groups within one source.
    [[0, 4], [1, 3, 5], [2]],  # Skipped scene within one source.
    [[0, 1], [2, 4], [3, 5]],  # Mixed sources.
    [[0, 2, 4], [1, 3, "unknown"]],
])
def test_grouping_rejects_invalid_evidence_in_interleaved_sources(groups):
    with pytest.raises(LLMError):
        validate_grouping({"groups": [group([f"visual:{i}" for i in refs]) for refs in groups]},
                          INTERLEAVED_RECORDS)


def test_support_requires_a_past_day_and_same_source_for_every_candidate():
    context = {"local_date": "2026-10-05", "candidates": [{"source_key": "vision:test"}],
               "evidence": [{"evidence_ref": "past:0", "source_key": "vision:test", "local_date": "2026-10-04"}],
               "existing_memories": []}
    payload = {"decisions": [support_decision()]}
    validate_support(payload, context)
    for key, value in (("local_date", "2026-10-05"), ("source_key", "vision:other")):
        invalid = deepcopy(context)
        invalid["evidence"][0][key] = value
        with pytest.raises(LLMError):
            validate_support(payload, invalid)
    for decisions in ([], [payload["decisions"][0], payload["decisions"][0]],
                      [support_decision(refs=["unknown"])], [support_decision(indexes=[True])]):
        with pytest.raises(LLMError):
            validate_support({"decisions": decisions}, context)


def test_failed_semantic_grouping_keeps_original_observations_and_records_failure():
    runner = ServiceVisualDailyMixin()
    records = [{"visual_observation_id": f"visual:{i}", "retention_status": "active"}
               for i in range(3)]
    runner.store = SimpleNamespace(
        list_visual_observation_records_for_date=lambda **kwargs: records,
        upsert_daily_visual_digest=Mock())
    runner._runtime_state_lock = threading.RLock()
    runner._visual_daily_runtime_state = {"current_digest_id": None}
    runner._now_iso = lambda: "2026-10-05T09:00:00+09:00"
    runner._visual_daily_groups = Mock(side_effect=LLMError("semantic review unavailable"))
    with pytest.raises(LLMError):
        runner._run_visual_daily_digest(memory_set_id="memory:test", local_date="2026-10-04")
    saved = runner.store.upsert_daily_visual_digest.call_args.kwargs
    assert saved["updated_records"] == []
    assert saved["digest"]["result_status"] == "failed"
    assert saved["digest"]["failure_reason"] == "visual_daily_grouping_failed"
    assert all(r["retention_status"] == "active" for r in records)
    assert runner._visual_daily_runtime_state["current_digest_id"] is None


def test_failed_cross_day_review_does_not_create_memory_actions():
    runner = ServiceVisualDailyMixin()
    runner.store = SimpleNamespace(upsert_daily_visual_digest=Mock())
    runner.memory = SimpleNamespace(action_resolver=SimpleNamespace(resolve_visual_daily_memory_actions=Mock()))
    runner._now_iso = lambda: "2026-10-05T09:00:00+09:00"
    runner._visual_daily_repeated_support = Mock(side_effect=LLMError("semantic review unavailable"))
    digest = {"digest_id": "digest:test", "memory_set_id": "memory:test", "memory_candidate_summaries": [
        {"summary_text": "観測要約。", "source_key": "vision:test"}]}
    with pytest.raises(LLMError):
        runner._promote_visual_daily_digest_memory_candidates(digest=digest, state={})
    runner.memory.action_resolver.resolve_visual_daily_memory_actions.assert_not_called()
    saved = runner.store.upsert_daily_visual_digest.call_args.kwargs
    assert saved["updated_records"] == []
    assert saved["digest"]["memory_promotion"]["result_status"] == "failed"
    assert saved["digest"]["memory_promotion"]["failure_reason"] == "visual_daily_support_failed"


def test_review_includes_canonical_memory_without_changing_observation_topics(promotion_runner):
    runner = promotion_runner
    first = run_promotion(runner, "pending_digest")
    unit_id, = first["memory_promotion"]["promoted_memory_unit_ids"]
    digest = seed_visual_digest(runner, "2026-10-06", summary="今日だけの詳細を含む観測。")
    candidates = digest["memory_candidate_summaries"]
    before = deepcopy(candidates)
    runner.llm.generate_visual_daily_support.return_value = {"decisions": [support_decision(relation="same")]}
    support = runner._visual_daily_repeated_support(digest=digest, candidates=candidates, state=runner.store.read_state())
    context = runner.llm.generate_visual_daily_support.call_args.kwargs["context"]
    assert candidates == before
    assert context["existing_memories"][0]["summary_text"] == support_decision()["shared_summary_text"]
    assert unit_id not in json.dumps(context)
    assert "topic_ref" not in json.dumps(context)
    assert support["memories"]["memory:0"]["memory_unit"]["memory_unit_id"] == unit_id
    assert digest["support_review"]["references"]["memory_unit_ids"] == {"memory:0": unit_id}


MEMORY_SET = "memory_set:default"
CURRENT_DATE = "2026-10-05"


@pytest.mark.parametrize("desktop_first", [False, True])
def test_interleaved_daily_review_compresses_each_source_with_one_completion(tmp_path, monkeypatch, desktop_first):
    runner = OtomeKairoService.__new__(OtomeKairoService)
    runner.store = FileStore(tmp_path)
    runner.llm = LLMClient()
    runner.memory = SimpleNamespace(action_resolver=MemoryActionResolver(store=runner.store, llm=runner.llm))
    runner._runtime_state_lock = threading.RLock()
    runner._visual_daily_runtime_state = {"current_digest_id": None}
    runner._now_iso = lambda: "2026-10-06T09:00:00+09:00"
    state = runner.store.read_state()
    state["model_presets"][state["selected_model_preset_id"]]["model"] = "test-model"
    monkeypatch.setattr(runner.store, "read_state", lambda: state)
    records = [{
        "visual_observation_id": f"visual:{i}", "memory_set_id": MEMORY_SET,
        "cycle_id": f"cycle:{i // 2}", "observed_at": f"{CURRENT_DATE}T09:0{i // 2}:00+09:00",
        "source_kind": "camera" if i % 2 == 0 else "desktop",
        "vision_source_id": "vision_source:camera" if i % 2 == 0 else "vision_source:desktop",
        "image_input_kind": "vision_capture_result", "confidence_hint": "medium",
        "retention_status": "active", "detailed_summary_text": f"省略せず渡す観測本文 {i}。",
    } for i in range(8)]
    with runner.store._memory_db() as conn:
        for record in records:
            runner.store._insert_visual_observation_record(conn, record)
    camera_groups = [group(["visual:0", "visual:2", "visual:4"]), group(["visual:6"])]
    desktop_group = group(["visual:1", "visual:3", "visual:5", "visual:7"])
    review = {"groups": [desktop_group, *camera_groups] if desktop_first else [*camera_groups, desktop_group]}
    complete = Mock(return_value=json.dumps(review))
    monkeypatch.setattr("otomekairo.llm.client.complete_text", complete)

    runner._run_visual_daily_digest(memory_set_id=MEMORY_SET, local_date=CURRENT_DATE)

    complete.assert_called_once()
    context = json.loads(complete.call_args.kwargs["messages"][1]["content"])
    assert context["source_sequences"] == [
        {"source_key": f"vision:vision_source:{source}", "records": [{
            "visual_observation_id": records[i]["visual_observation_id"],
            "source_key": f"vision:vision_source:{source}",
            "observed_at": records[i]["observed_at"],
            "detailed_summary_text": records[i]["detailed_summary_text"],
        } for i in indices]}
        for source, indices in (("camera", [0, 2, 4, 6]), ("desktop", [1, 3, 5, 7]))
    ]
    saved = runner.store.get_daily_visual_digest(memory_set_id=MEMORY_SET, local_date=CURRENT_DATE)
    assert saved["result_status"] == "succeeded"
    assert saved["record_count"] == 8
    assert saved["group_count"] == 3
    assert saved["compressed_count"] == 3
    assert saved["retained_count"] == 5
    assert [g["representative_visual_observation_id"] for g in saved["group_summaries"]] == [
        "visual:0", "visual:1", "visual:6"]
    updated = runner.store.list_visual_observation_records_for_date(memory_set_id=MEMORY_SET, local_date=CURRENT_DATE)
    by_id = {record["visual_observation_id"]: record for record in updated}
    assert {ref for ref, record in by_id.items() if record["retention_status"] == "compressed"} == {
        "visual:2", "visual:3", "visual:5"}
    for index, refs in enumerate(([0, 2, 4], [1, 3, 5, 7], [6]), 1):
        group_id = runner._visual_daily_group_id(memory_set_id=MEMORY_SET, local_date=CURRENT_DATE, index=index)
        assert all(by_id[f"visual:{i}"]["duplicate_group_id"] == group_id for i in refs)
    assert all(by_id[record["visual_observation_id"]]["detailed_summary_text"] == record["detailed_summary_text"]
               for record in records)


def seed_visual_digest(runner, local_date, *, summary="検証用の観測要約。"):
    record = {
        "visual_observation_id": f"visual:{local_date}", "memory_set_id": MEMORY_SET,
        "cycle_id": f"cycle:{local_date}", "observed_at": f"{local_date}T09:00:00+09:00",
        "source_kind": "camera", "source_label": "検証用カメラ", "vision_source_id": "vision_source:test",
        "image_input_kind": "vision_capture_result", "confidence_hint": "medium",
        "retention_status": "active", "detailed_summary_text": summary,
    }
    digest = runner._build_visual_daily_digest(
        digest_id=runner._visual_daily_digest_id(memory_set_id=MEMORY_SET, local_date=local_date),
        memory_set_id=MEMORY_SET, local_date=local_date,
        started_at=runner._now_iso(), finished_at=runner._now_iso(), records=[record],
        groups=[{
            "duplicate_group_id": runner._visual_daily_group_id(
                memory_set_id=MEMORY_SET, local_date=local_date, index=1),
            "source_key": runner._visual_daily_source_key(record), "records": [record],
            "summary_text": summary, "reason_summary": "検証用の整理根拠。",
        }],
    )
    record["duplicate_group_id"] = digest["group_summaries"][0]["duplicate_group_id"]
    record["daily_digest_id"] = digest["digest_id"]
    runner.store.upsert_daily_visual_digest(digest=digest, updated_records=[record])
    return digest


@pytest.fixture
def promotion_runner(tmp_path):
    runner = OtomeKairoService.__new__(OtomeKairoService)
    runner.store = FileStore(tmp_path)
    runner._runtime_state_lock = threading.RLock()
    runner._visual_daily_runtime_state = {"current_digest_id": None}
    runner._now_iso = lambda: "2026-10-06T09:00:00+09:00"
    runner.llm = Mock()
    runner.llm.generate_embeddings.side_effect = lambda **kw: [
        [1.0] + [0.0] * (kw["model_config"]["embedding_dimension"] - 1)
        for _ in kw["texts"]
    ]
    runner.memory = SimpleNamespace(
        action_resolver=MemoryActionResolver(store=runner.store, llm=runner.llm),
        vector_indexer=MemoryVectorIndexer(store=runner.store, llm=runner.llm),
    )
    previous = seed_visual_digest(runner, "2026-10-04")
    previous["memory_promotion"]["result_status"] = "skipped"
    runner.store.upsert_daily_visual_digest(digest=previous, updated_records=[])
    seed_visual_digest(runner, CURRENT_DATE)
    runner.llm.generate_visual_daily_grouping.return_value = {
        "groups": [group([f"visual:{CURRENT_DATE}"])]}
    runner.llm.generate_visual_daily_support.return_value = {"decisions": [support_decision()]}
    return runner


def run_promotion(runner, entrypoint):
    if entrypoint == "new_digest":
        return runner._run_visual_daily_digest(memory_set_id=MEMORY_SET, local_date=CURRENT_DATE)
    runner._run_due_visual_daily_promotions(memory_set_id=MEMORY_SET)
    return runner.store.get_daily_visual_digest(memory_set_id=MEMORY_SET, local_date=CURRENT_DATE)


@pytest.mark.parametrize("entrypoint", ["new_digest", "pending_digest"])
def test_supported_visual_candidate_is_persisted_with_no_synthetic_object(promotion_runner, entrypoint):
    runner = promotion_runner
    result = run_promotion(runner, entrypoint)
    saved = runner.store.get_daily_visual_digest(memory_set_id=MEMORY_SET, local_date=CURRENT_DATE)
    promotion = saved["memory_promotion"]
    assert promotion["result_status"] == "succeeded"
    assert result["memory_promotion"] == promotion
    unit, = runner.store.list_memory_units_by_id(
        memory_set_id=MEMORY_SET, memory_unit_ids=promotion["promoted_memory_unit_ids"])
    previous = runner.store.get_daily_visual_digest(memory_set_id=MEMORY_SET, local_date="2026-10-04")
    topic_ref = previous["memory_candidate_summaries"][0]["topic_ref"]
    assert unit["scope_type"] == "topic"
    assert unit["scope_key"] == unit["subject_ref"] != topic_ref
    assert unit["summary_text"] == support_decision()["shared_summary_text"]
    assert unit["object_ref_or_value"] is None
    assert unit["qualifiers"]["object_kind"] == "none"
    assert unit["status"] == "inferred"
    assert 0 < unit["confidence"] <= 0.55
    assert 0 < unit["salience"] <= 0.45
    assert unit["qualifiers"]["digest_id"] == saved["digest_id"]
    assert unit["qualifiers"]["representative_visual_observation_id"] == f"visual:{CURRENT_DATE}"
    assert unit["qualifiers"]["duplicate_group_id"] == saved["group_summaries"][0]["duplicate_group_id"]
    assert unit["qualifiers"]["support_group_refs"] == [previous["group_summaries"][0]["duplicate_group_id"]]
    assert {item["local_date"] for item in unit["qualifiers"]["visual_evidence"]} == {"2026-10-04", CURRENT_DATE}
    runner.llm.generate_visual_daily_support.assert_called_once()
    # Only the saved memory's vector is embedded, with no comparison embedding.
    runner.llm.generate_embeddings.assert_called_once()
    assert runner.store.list_vector_index_metadata(
        memory_set_id=MEMORY_SET, sources=[("memory_unit", unit["memory_unit_id"])])


@pytest.mark.parametrize("entrypoint", ["new_digest", "pending_digest"])
def test_unsupported_visual_candidate_creates_no_memory(promotion_runner, monkeypatch, entrypoint):
    runner = promotion_runner
    runner.llm.generate_visual_daily_support.return_value = {"decisions": [support_decision(status="insufficient")]}
    persist = Mock(wraps=runner.store.persist_memory_actions)
    monkeypatch.setattr(runner.store, "persist_memory_actions", persist)
    result = run_promotion(runner, entrypoint)
    assert result["memory_promotion"]["result_status"] == "skipped"
    assert result["memory_promotion"]["skipped_candidate_count"] == 1
    assert result["memory_promotion"]["promoted_memory_unit_ids"] == []
    persist.assert_not_called()
    runner.llm.generate_embeddings.assert_not_called()


@pytest.mark.parametrize("relation", ["same", "refine"])
def test_later_supported_visual_candidate_updates_only_reviewed_memory(promotion_runner, monkeypatch, relation):
    runner = promotion_runner
    first = run_promotion(runner, "pending_digest")
    original_ids = first["memory_promotion"]["promoted_memory_unit_ids"]
    original, = runner.store.list_memory_units_by_id(memory_set_id=MEMORY_SET, memory_unit_ids=original_ids)
    seed_visual_digest(runner, "2026-10-06", summary="後日の検証用観測要約。")
    refined = "元の主張を保持した根拠のある補足。" if relation == "refine" else None
    runner.llm.generate_visual_daily_support.return_value = {"decisions": [support_decision(relation=relation, refined=refined)]}
    persist = Mock(wraps=runner.store.persist_memory_actions)
    monkeypatch.setattr(runner.store, "persist_memory_actions", persist)
    runner._run_due_visual_daily_promotions(memory_set_id=MEMORY_SET)
    later = runner.store.get_daily_visual_digest(memory_set_id=MEMORY_SET, local_date="2026-10-06")
    assert later["memory_promotion"]["result_status"] == "succeeded"
    assert later["memory_promotion"]["promoted_memory_unit_ids"] == original_ids
    action, = persist.call_args.kwargs["memory_actions"]
    assert action["operation"] == ("refine" if relation == "refine" else "reinforce")
    assert action["memory_unit_id"] == original_ids[0]
    unit, = runner.store.find_memory_units_for_compare(
        memory_set_id=MEMORY_SET, memory_type="interpretation", scope_type="topic",
        scope_key=action["memory_unit"]["scope_key"], subject_ref=action["memory_unit"]["subject_ref"],
        predicate="visual_daily_pattern")
    assert unit["memory_unit_id"] == original_ids[0]
    assert unit["qualifiers"]["digest_id"] == later["digest_id"]
    assert unit["formed_at"] == original["formed_at"]
    assert unit["summary_text"] == (refined if relation == "refine" else original["summary_text"])
    assert {item["local_date"] for item in unit["qualifiers"]["visual_evidence"]} == {"2026-10-04", CURRENT_DATE, "2026-10-06"}


@pytest.mark.parametrize("entrypoint", ["new_digest", "pending_digest"])
@pytest.mark.parametrize("stage", ["support", "candidate", "actions"])
def test_promotion_preparation_failure_is_saved_without_replacing_reason(
    promotion_runner, monkeypatch, entrypoint, stage,
):
    runner = promotion_runner
    error = LLMError(f"{stage} unavailable")
    if stage == "support":
        runner.llm.generate_visual_daily_support.side_effect = error
    elif stage == "candidate":
        monkeypatch.setattr(runner, "_visual_daily_memory_candidate", Mock(side_effect=error))
    else:
        monkeypatch.setattr(runner.memory.action_resolver, "resolve_visual_daily_memory_actions", Mock(side_effect=error))
    persist = Mock(wraps=runner.store.persist_memory_actions)
    monkeypatch.setattr(runner.store, "persist_memory_actions", persist)
    with pytest.raises(LLMError, match=f"{stage} unavailable"):
        run_promotion(runner, entrypoint)
    saved = runner.store.get_daily_visual_digest(memory_set_id=MEMORY_SET, local_date=CURRENT_DATE)
    assert saved["result_status"] == "succeeded"
    assert saved["memory_promotion"]["result_status"] == "failed"
    assert saved["memory_promotion"]["failure_reason"] == (
        "visual_daily_support_failed" if stage == "support" else "visual_daily_promotion_failed")
    assert saved["memory_promotion"]["promoted_memory_unit_ids"] == []
    if stage != "support":
        assert saved["support_review"]["decisions"][0]["past_support_refs"]
    record, = runner.store.list_visual_observation_records_for_date(
        memory_set_id=MEMORY_SET, local_date=CURRENT_DATE)
    assert record["detailed_summary_text"] == "検証用の観測要約。"
    assert record["retention_status"] == "active"
    assert runner._visual_daily_runtime_state["current_digest_id"] is None
    persist.assert_not_called()
    runner.llm.generate_embeddings.assert_not_called()


@pytest.mark.parametrize("entrypoint", ["new_digest", "pending_digest"])
@pytest.mark.parametrize("index", ["vector", "relation"])
def test_index_failure_keeps_saved_memory_ids(promotion_runner, monkeypatch, entrypoint, index):
    runner = promotion_runner
    if index == "vector":
        monkeypatch.setattr(runner.memory.vector_indexer, "sync", Mock(side_effect=LLMError("index unavailable")))
    else:
        monkeypatch.setattr(runner.store, "rebuild_relation_index", Mock(side_effect=LLMError("index unavailable")))
    result = run_promotion(runner, entrypoint)
    saved = runner.store.get_daily_visual_digest(memory_set_id=MEMORY_SET, local_date=CURRENT_DATE)
    promotion = saved["memory_promotion"]
    assert result["memory_promotion"] == promotion
    assert saved["result_status"] == "succeeded"
    assert promotion["result_status"] == "failed"
    assert promotion["failure_reason"] == f"{index}_index_sync: index unavailable"
    unit, = runner.store.list_memory_units_by_id(
        memory_set_id=MEMORY_SET, memory_unit_ids=promotion["promoted_memory_unit_ids"])
    assert unit["object_ref_or_value"] is None
    if index == "relation":
        assert promotion["relation_index_sync"]["result_status"] == "failed"


def test_promotion_status_changes_only_after_it_is_saved(promotion_runner, monkeypatch):
    runner = promotion_runner
    digest = runner.store.get_daily_visual_digest(memory_set_id=MEMORY_SET, local_date=CURRENT_DATE)
    original = deepcopy(digest)
    monkeypatch.setattr(runner.store, "upsert_daily_visual_digest", Mock(side_effect=RuntimeError("write failed")))
    with pytest.raises(RuntimeError, match="write failed"):
        runner._store_visual_daily_promotion_result(
            digest=digest, result_status="failed", promoted_memory_unit_ids=[],
            skipped_candidate_count=0, failure_reason="visual_daily_support_failed", relation_index_sync=None)
    assert digest == original


@pytest.mark.parametrize("field,value", [
    ("support_status", "unknown"), ("relation", "replace"), ("shared_summary_text", " "),
    ("past_support_refs", []), ("past_support_refs", ["past:0", "past:0"]),
    ("target_memory_ref", "memory:unknown"), ("refined_summary_text", "未審査の統合本文"),
    ("candidate_indexes", []), ("candidate_indexes", [0, 0]), ("candidate_indexes", [1]),
    ("source_claims", []), ("source_claims", [{"group_ref": "unknown", "claim_text": "主張。"}]),
])
def test_support_rejects_inconsistent_closed_contract(field, value):
    context = {"local_date": CURRENT_DATE, "candidates": [{"source_key": "vision:test"}],
               "evidence": [{"evidence_ref": "past:0", "local_date": "2026-10-04", "source_key": "vision:test"}],
               "existing_memories": []}
    decision = support_decision()
    decision[field] = value
    with pytest.raises(LLMError):
        validate_support({"decisions": [decision]}, context)


def test_support_requires_known_same_source_target_and_unique_update():
    context = {"local_date": CURRENT_DATE, "candidates": [{"source_key": "vision:test"}] * 2,
               "evidence": [{"evidence_ref": "past:0", "local_date": "2026-10-04", "source_key": "vision:test"}],
               "existing_memories": [{"memory_ref": "memory:0", "source_key": "vision:test"}]}
    validate_support({"decisions": [support_decision(indexes=[0, 1], relation="same")]}, context)
    for decisions in (
        [support_decision(indexes=[0], relation="same"), support_decision(indexes=[1], relation="same")],
    ):
        with pytest.raises(LLMError):
            validate_support({"decisions": decisions}, context)
    context["existing_memories"][0]["source_key"] = "vision:other"
    with pytest.raises(LLMError):
        validate_support({"decisions": [support_decision(indexes=[0, 1], relation="same")]}, context)
    context["candidates"][1] = {"source_key": "vision:other"}
    with pytest.raises(LLMError):
        validate_support({"decisions": [support_decision(indexes=[0, 1])]}, context)


@pytest.mark.parametrize("relation,label", [("parallel", "about_same_scope"), ("conflict", "contradicts"), ("new", None)])
def test_distinct_claim_keeps_old_canonical_and_only_explicit_relation(promotion_runner, relation, label):
    runner = promotion_runner
    first = run_promotion(runner, "pending_digest")
    original_ids = first["memory_promotion"]["promoted_memory_unit_ids"]
    original, = runner.store.list_memory_units_by_id(memory_set_id=MEMORY_SET, memory_unit_ids=original_ids)
    seed_visual_digest(runner, "2026-10-06", summary="別の主張を支持する後日の観測。")
    runner.llm.generate_visual_daily_support.return_value = {"decisions": [support_decision(
        relation=relation, shared="原観測の一部に絞った別の主張。") ]}
    runner._run_due_visual_daily_promotions(memory_set_id=MEMORY_SET)
    later = runner.store.get_daily_visual_digest(memory_set_id=MEMORY_SET, local_date="2026-10-06")
    assert later["memory_promotion"]["result_status"] == "succeeded"
    new_id, = later["memory_promotion"]["promoted_memory_unit_ids"]
    assert new_id not in original_ids
    unchanged, = runner.store.list_memory_units_by_id(memory_set_id=MEMORY_SET, memory_unit_ids=original_ids)
    assert unchanged == original
    created, = runner.store.list_memory_units_by_id(memory_set_id=MEMORY_SET, memory_unit_ids=[new_id])
    assert created["scope_key"] != original["scope_key"]
    assert created["summary_text"] == "原観測の一部に絞った別の主張。"
    links = runner.store.list_memory_links_for_recall(memory_set_id=MEMORY_SET, memory_unit_ids=[new_id])
    assert {link["label"] for link in links} == ({label} if label else set())
    assert all(link["source_memory_unit_id"] == new_id and link["target_memory_unit_id"] == original_ids[0] for link in links)


def table_counts(store):
    with store._memory_db() as conn:
        return {name: conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                for name in ("memory_units", "revisions", "memory_links")}


@pytest.mark.parametrize("change", ["update", "delete"])
def test_canonical_changed_during_llm_review_is_not_overwritten(promotion_runner, change):
    runner = promotion_runner
    first = run_promotion(runner, "pending_digest")
    original_id, = first["memory_promotion"]["promoted_memory_unit_ids"]
    seed_visual_digest(runner, "2026-10-06")
    after_concurrent_write = {}

    def review(**kwargs):
        with runner.store._memory_db() as conn:
            if change == "update":
                row = conn.execute("SELECT payload_json FROM memory_units WHERE memory_unit_id = ?", (original_id,)).fetchone()
                updated = json.loads(row[0])
                updated["summary_text"] = "別の処理が確定させた現在の正本。"
                runner.store._upsert_memory_unit(conn, updated)
            else:
                conn.execute("DELETE FROM memory_units WHERE memory_unit_id = ?", (original_id,))
        after_concurrent_write.update(table_counts(runner.store))
        return {"decisions": [support_decision(relation="same")]}

    runner.llm.generate_visual_daily_support.side_effect = review
    runner._run_due_visual_daily_promotions(memory_set_id=MEMORY_SET)
    result = runner.store.get_daily_visual_digest(memory_set_id=MEMORY_SET, local_date="2026-10-06")
    assert result["result_status"] == "succeeded"
    assert result["memory_promotion"]["result_status"] == "failed"
    assert "正本が変更または削除" in result["memory_promotion"]["failure_reason"]
    assert result["memory_promotion"]["promoted_memory_unit_ids"] == []
    assert table_counts(runner.store) == after_concurrent_write
    units = runner.store.list_memory_units_by_id(memory_set_id=MEMORY_SET, memory_unit_ids=[original_id])
    assert (units[0]["summary_text"] == "別の処理が確定させた現在の正本。") if change == "update" else units == []


def test_link_write_failure_rolls_back_memory_and_revision(promotion_runner, monkeypatch):
    runner = promotion_runner
    run_promotion(runner, "pending_digest")
    seed_visual_digest(runner, "2026-10-06")
    runner.llm.generate_visual_daily_support.return_value = {"decisions": [support_decision(relation="conflict")]}
    before = table_counts(runner.store)
    monkeypatch.setattr(runner.store.memory_store, "_upsert_memory_link", Mock(side_effect=RuntimeError("link write failed")))
    runner._run_due_visual_daily_promotions(memory_set_id=MEMORY_SET)
    result = runner.store.get_daily_visual_digest(memory_set_id=MEMORY_SET, local_date="2026-10-06")
    assert result["memory_promotion"]["result_status"] == "failed"
    assert table_counts(runner.store) == before


def test_duplicate_current_claims_are_merged_before_persisting(promotion_runner):
    runner = promotion_runner
    digest = runner.store.get_daily_visual_digest(memory_set_id=MEMORY_SET, local_date=CURRENT_DATE)
    record, = runner.store.list_visual_observation_records_for_date(memory_set_id=MEMORY_SET, local_date=CURRENT_DATE)
    group_id = runner._visual_daily_group_id(memory_set_id=MEMORY_SET, local_date=CURRENT_DATE, index=2)
    record.update(visual_observation_id="visual:second", duplicate_group_id=group_id,
                  observed_at=f"{CURRENT_DATE}T10:00:00+09:00")
    digest["group_summaries"].append({**digest["group_summaries"][0], "duplicate_group_id": group_id,
                                     "representative_visual_observation_id": "visual:second"})
    digest["memory_candidate_summaries"] = runner._visual_daily_memory_candidate_summaries(digest["group_summaries"])
    runner.store.upsert_daily_visual_digest(digest=digest, updated_records=[record])
    runner.llm.generate_visual_daily_support.return_value = {"decisions": [support_decision(indexes=[0, 1])]}
    result = run_promotion(runner, "pending_digest")
    assert result["memory_promotion"]["result_status"] == "succeeded"
    assert table_counts(runner.store) == {"memory_units": 1, "revisions": 1, "memory_links": 0}
    runner.llm.generate_visual_daily_support.assert_called_once()
    unit, = runner.store.list_memory_units_by_id(
        memory_set_id=MEMORY_SET, memory_unit_ids=result["memory_promotion"]["promoted_memory_unit_ids"])
    assert len(unit["qualifiers"]["visual_evidence"]) == 3


@pytest.mark.parametrize("status,relation", [("insufficient", "uncertain"), ("unsuitable", "uncertain"), ("supported", "uncertain")])
def test_normal_skips_do_not_generate_or_embed_memory(promotion_runner, status, relation):
    runner = promotion_runner
    runner.llm.generate_visual_daily_support.return_value = {"decisions": [support_decision(status=status, relation=relation)]}
    result = run_promotion(runner, "pending_digest")
    assert result["memory_promotion"]["result_status"] == "skipped"
    assert table_counts(runner.store) == {"memory_units": 0, "revisions": 0, "memory_links": 0}
    runner.llm.generate_visual_daily_support.assert_called_once()
    runner.llm.generate_embeddings.assert_not_called()


def test_missing_referenced_canonical_fails_before_generation(promotion_runner):
    runner = promotion_runner
    previous = runner.store.get_daily_visual_digest(memory_set_id=MEMORY_SET, local_date="2026-10-04")
    previous["memory_promotion"]["promoted_memory_unit_ids"] = ["memory_unit:missing"]
    runner.store.upsert_daily_visual_digest(digest=previous, updated_records=[])
    with pytest.raises(ValueError, match="正本がありません"):
        run_promotion(runner, "pending_digest")
    runner.llm.generate_visual_daily_support.assert_not_called()
    assert table_counts(runner.store) == {"memory_units": 0, "revisions": 0, "memory_links": 0}


@pytest.mark.parametrize("tamper", ["missing_review", "operation", "target", "same_text", "duplicate", "missing_snapshots"])
def test_store_rejects_actions_outside_review_atomically(promotion_runner, monkeypatch, tamper):
    runner = promotion_runner
    first = run_promotion(runner, "pending_digest")
    original, = runner.store.list_memory_units_by_id(memory_set_id=MEMORY_SET, memory_unit_ids=first["memory_promotion"]["promoted_memory_unit_ids"])
    seed_visual_digest(runner, "2026-10-06")
    runner.llm.generate_visual_daily_support.return_value = {"decisions": [support_decision(relation="same")]}
    captured = {}

    def capture(**kwargs):
        captured.update(deepcopy(kwargs))
        raise RuntimeError("capture without write")

    persist = runner.store.persist_memory_actions
    monkeypatch.setattr(runner.store, "persist_memory_actions", capture)
    runner._run_due_visual_daily_promotions(memory_set_id=MEMORY_SET)
    action = captured["memory_actions"][0]
    if tamper == "missing_review":
        del action["visual_daily_review"]
    elif tamper == "operation":
        action["operation"] = "revoke"
    elif tamper == "target":
        action["visual_daily_review"]["target_memory_unit_id"] = "memory_unit:unknown"
    elif tamper == "same_text":
        action["memory_unit"]["summary_text"] = "別の意味に変えた本文。"
    elif tamper == "duplicate":
        captured["memory_actions"].append(deepcopy(action))
    else:
        del captured["expected_memory_units"]
    before = table_counts(runner.store)
    with pytest.raises(ValueError):
        persist(**captured)
    assert table_counts(runner.store) == before
    unchanged, = runner.store.list_memory_units_by_id(memory_set_id=MEMORY_SET, memory_unit_ids=[original["memory_unit_id"]])
    assert unchanged == original


def test_support_client_completes_once_with_shared_canonical_context(promotion_runner, monkeypatch):
    runner = promotion_runner
    state = runner.store.read_state()
    model = {**state["model_presets"][state["selected_model_preset_id"]], "model": "test-model"}
    persona = build_persona_context(state["personas"][state["selected_persona_id"]], role="visual_daily_support")
    context = {"local_date": CURRENT_DATE, "candidates": [{"candidate_index": 0, "group_ref": "current:0", "source_key": "vision:test", "summary_text": "当日の観測。"}],
               "evidence": [{"evidence_ref": "past:0", "local_date": "2026-10-04", "source_key": "vision:test", "summary_text": "過去の観測。"}],
               "existing_memories": []}
    complete = Mock(return_value=json.dumps({"decisions": [support_decision()]}))
    monkeypatch.setattr("otomekairo.llm.client.complete_text", complete)
    result = LLMClient().generate_visual_daily_support(model_config=model, persona_context=persona, context=context)
    assert result["decisions"][0]["relation"] == "new"
    complete.assert_called_once()
    assert json.loads(complete.call_args.kwargs["messages"][1]["content"])["evidence"] == context["evidence"]
    sent = json.loads(complete.call_args.kwargs["messages"][1]["content"])
    assert sent["candidates"][0]["allowed_past_refs"] == ["past:0"]
    assert sent["candidates"][0]["allowed_memory_refs"] == []
    assert "allowed_past_refs" not in context["candidates"][0]


@pytest.mark.parametrize("relation,comparison_update,expected", [
    ("refine", {"existing_claims": [{"claim_text": "保持される内容。", "entailed_by_shared": True, "unconfirmed_existing_parts": []},
                                   {"claim_text": "共通主張に収まらない内容。", "entailed_by_shared": False,
                                    "unconfirmed_existing_parts": ["共通主張に収まらない内容。"]}]}, "parallel"),
    ("conflict", {"same_conditions_confirmed": False}, "parallel"),
    ("same", {"shared_claims": [{"claim_text": "共通主張が加える新しい情報。", "entailed_by_existing": False,
                                "unconfirmed_shared_parts": ["共通主張が加える新しい情報。"]}]}, "refine"),
    ("refine", {"shared_claims": [{"claim_text": "同義の情報。", "entailed_by_existing": True, "unconfirmed_shared_parts": []}]}, "same"),
])
def test_relation_must_agree_with_explicit_semantic_comparison(relation, comparison_update, expected):
    context = {"local_date": CURRENT_DATE, "candidates": [{"source_key": "vision:test"}],
               "evidence": [{"evidence_ref": "past:0", "local_date": "2026-10-04", "source_key": "vision:test"}],
               "existing_memories": [{"memory_ref": "memory:0", "source_key": "vision:test"}]}
    decision = support_decision(relation=relation)
    decision["memory_comparison"].update(comparison_update)
    with pytest.raises(LLMError, match=f"relationは{expected}"):
        validate_support({"decisions": [decision]}, context)


def test_inconsistent_partial_update_is_rejected_before_memory_write(promotion_runner):
    runner = promotion_runner
    first = run_promotion(runner, "pending_digest")
    original, = runner.store.list_memory_units_by_id(memory_set_id=MEMORY_SET, memory_unit_ids=first["memory_promotion"]["promoted_memory_unit_ids"])
    seed_visual_digest(runner, "2026-10-06")
    decision = support_decision(relation="refine")
    decision["memory_comparison"]["existing_claims"].append({"claim_text": "今回再確認できない旧理解。", "entailed_by_shared": False,
                                                          "unconfirmed_existing_parts": ["今回再確認できない旧理解。"]})
    runner.llm.generate_visual_daily_support.return_value = {"decisions": [decision]}
    before = table_counts(runner.store)
    with pytest.raises(LLMError, match="relationはparallel"):
        runner._run_due_visual_daily_promotions(memory_set_id=MEMORY_SET)
    assert table_counts(runner.store) == before
    unchanged, = runner.store.list_memory_units_by_id(memory_set_id=MEMORY_SET, memory_unit_ids=[original["memory_unit_id"]])
    assert unchanged == original


@pytest.mark.parametrize("status", ["compressed", "excluded", "missing"])
def test_unavailable_or_unretained_representative_cannot_ground_promotion(promotion_runner, status):
    runner = promotion_runner
    record, = runner.store.list_visual_observation_records_for_date(memory_set_id=MEMORY_SET, local_date=CURRENT_DATE)
    with runner.store._memory_db() as conn:
        if status == "missing":
            conn.execute("DELETE FROM visual_observation_records WHERE visual_observation_id = ?", (record["visual_observation_id"],))
        else:
            record["retention_status"] = status
            runner.store._insert_visual_observation_record(conn, record)
    result = run_promotion(runner, "pending_digest")
    assert result["result_status"] == "succeeded"
    assert result["memory_promotion"]["result_status"] == "failed"
    assert table_counts(runner.store) == {"memory_units": 0, "revisions": 0, "memory_links": 0}


def test_observation_evidence_is_provenance_not_embedding_content():
    from otomekairo.memory.utils import build_memory_unit_semantic_text
    content = {"summary_text": "複数日の観測でPC作業が見られた。", "predicate": "visual_daily_pattern", "qualifiers": {}}
    assert build_memory_unit_semantic_text(content) == build_memory_unit_semantic_text({
        **content, "qualifiers": {"visual_evidence": [{"duplicate_group_id": "group:trace", "local_date": "2026-10-04"}]}})
