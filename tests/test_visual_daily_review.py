from copy import deepcopy
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from otomekairo.llm.contracts import LLMError
from otomekairo.llm.visual_daily import validate_grouping, validate_support
from otomekairo.memory.actions import MemoryActionResolver
from otomekairo.memory.vector import MemoryVectorIndexer
from otomekairo.service.app import OtomeKairoService
from otomekairo.service.visual_daily import ServiceVisualDailyMixin
from otomekairo.store.file_store import FileStore


RECORDS = [{"visual_observation_id": f"visual:{i}", "source_key": "vision:test"} for i in range(3)]


def group(refs):
    return {"observation_ids": refs, "summary_text": "観測の要約。", "reason_summary": "根拠の説明。"}


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


def test_support_requires_a_past_day_and_same_source_for_every_candidate():
    context = {"local_date": "2026-10-05", "candidates": [{"source_key": "vision:test"}],
               "evidence": [{"evidence_ref": "group:old", "source_key": "vision:test", "local_date": "2026-10-04"}]}
    payload = {"decisions": [{"candidate_index": 0, "support_refs": ["group:old"], "reason_summary": "別日の根拠。"}]}
    validate_support(payload, context)
    for key, value in (("local_date", "2026-10-05"), ("source_key", "vision:other")):
        invalid = deepcopy(context)
        invalid["evidence"][0][key] = value
        with pytest.raises(LLMError):
            validate_support(payload, invalid)
    for decisions in ([], [payload["decisions"][0], payload["decisions"][0]],
                      [{"candidate_index": 0, "support_refs": ["unknown"], "reason_summary": "理由。"}]):
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
    runner.memory = SimpleNamespace(action_resolver=SimpleNamespace(resolve_memory_actions_batch=Mock()))
    runner._now_iso = lambda: "2026-10-05T09:00:00+09:00"
    runner._visual_daily_repeated_support = Mock(side_effect=LLMError("semantic review unavailable"))
    digest = {"digest_id": "digest:test", "memory_set_id": "memory:test", "memory_candidate_summaries": [
        {"summary_text": "観測要約。", "source_key": "vision:test"}]}
    with pytest.raises(LLMError):
        runner._promote_visual_daily_digest_memory_candidates(digest=digest, state={})
    runner.memory.action_resolver.resolve_memory_actions_batch.assert_not_called()
    saved = runner.store.upsert_daily_visual_digest.call_args.kwargs
    assert saved["updated_records"] == []
    assert saved["digest"]["memory_promotion"]["result_status"] == "failed"
    assert saved["digest"]["memory_promotion"]["failure_reason"] == "visual_daily_support_failed"


def test_llm_support_keeps_topic_identity_across_paraphrases_but_not_opposites():
    runner = ServiceVisualDailyMixin()
    runner.store = SimpleNamespace(list_daily_visual_digests=lambda **kwargs: [{
        "digest_id": "digest:old", "local_date": "2026-10-04", "result_status": "succeeded",
        "memory_candidate_summaries": [{"duplicate_group_id": "group:old", "source_key": "vision:test",
                                       "summary_text": "机にはノートPCとカップがある。", "topic_ref": "topic:existing"}]}])
    runner.llm = SimpleNamespace(generate_visual_daily_support=Mock(return_value={"decisions": [
        {"candidate_index": 0, "support_refs": ["group:old"], "reason_summary": "同じ場面の言い換え。"},
        {"candidate_index": 1, "support_refs": [], "reason_summary": "カップの有無が異なる。"}]}))
    state = {"selected_model_preset_id": "model:test", "model_presets": {"model:test": {"model": "mock"}},
             "selected_persona_id": "persona:test", "personas": {"persona:test": {
                 "persona_id": "persona:test", "display_name": "試験人格", "persona_prompt": "観測根拠に沿って考える。"}}}
    candidates = [
        {"summary_text": "卓上にカップとラップトップが置かれている。", "source_key": "vision:test", "topic_ref": "topic:new-positive"},
        {"summary_text": "机にノートPCはあるがカップはない。", "source_key": "vision:test", "topic_ref": "topic:new-negative"},
    ]
    digest = {"memory_set_id": "memory:test", "local_date": "2026-10-05"}
    support = runner._visual_daily_repeated_support(digest=digest, candidates=candidates, state=state)
    assert support == {0: ["group:old"], 1: []}
    assert candidates[0]["topic_ref"] == "topic:existing"
    assert candidates[1]["topic_ref"] == "topic:new-negative"
    assert digest["support_review"]["decisions"][0]["support_refs"] == ["group:old"]


MEMORY_SET = "memory_set:default"
CURRENT_DATE = "2026-10-05"


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
    runner.llm.generate_visual_daily_support.return_value = {"decisions": [{
        "candidate_index": 0,
        "support_refs": [previous["memory_candidate_summaries"][0]["duplicate_group_id"]],
        "reason_summary": "別日の観測による支持。",
    }]}
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
    assert unit["scope_key"] == unit["subject_ref"] == topic_ref
    assert unit["object_ref_or_value"] is None
    assert unit["qualifiers"]["object_kind"] == "none"
    assert unit["status"] == "inferred"
    assert 0 < unit["confidence"] <= 0.55
    assert 0 < unit["salience"] <= 0.45
    assert unit["qualifiers"]["digest_id"] == saved["digest_id"]
    assert unit["qualifiers"]["representative_visual_observation_id"] == f"visual:{CURRENT_DATE}"
    assert unit["qualifiers"]["duplicate_group_id"] == saved["group_summaries"][0]["duplicate_group_id"]
    assert unit["qualifiers"]["support_group_refs"] == [previous["group_summaries"][0]["duplicate_group_id"]]
    assert runner.store.list_vector_index_metadata(
        memory_set_id=MEMORY_SET, sources=[("memory_unit", unit["memory_unit_id"])])


@pytest.mark.parametrize("entrypoint", ["new_digest", "pending_digest"])
def test_unsupported_visual_candidate_creates_no_memory(promotion_runner, monkeypatch, entrypoint):
    runner = promotion_runner
    runner.llm.generate_visual_daily_support.return_value["decisions"][0]["support_refs"] = []
    persist = Mock(wraps=runner.store.persist_memory_actions)
    monkeypatch.setattr(runner.store, "persist_memory_actions", persist)
    result = run_promotion(runner, entrypoint)
    assert result["memory_promotion"]["result_status"] == "skipped"
    assert result["memory_promotion"]["skipped_candidate_count"] == 1
    assert result["memory_promotion"]["promoted_memory_unit_ids"] == []
    persist.assert_not_called()
    runner.llm.generate_embeddings.assert_not_called()


def test_later_supported_visual_candidate_updates_existing_memory(promotion_runner, monkeypatch):
    runner = promotion_runner
    first = run_promotion(runner, "pending_digest")
    original_ids = first["memory_promotion"]["promoted_memory_unit_ids"]
    seed_visual_digest(runner, "2026-10-06", summary="後日の検証用観測要約。")
    persist = Mock(wraps=runner.store.persist_memory_actions)
    monkeypatch.setattr(runner.store, "persist_memory_actions", persist)
    runner._run_due_visual_daily_promotions(memory_set_id=MEMORY_SET)
    later = runner.store.get_daily_visual_digest(memory_set_id=MEMORY_SET, local_date="2026-10-06")
    assert later["memory_promotion"]["result_status"] == "succeeded"
    assert later["memory_promotion"]["promoted_memory_unit_ids"] == original_ids
    action, = persist.call_args.kwargs["memory_actions"]
    assert action["operation"] == "refine"
    assert action["memory_unit_id"] == original_ids[0]
    unit, = runner.store.find_memory_units_for_compare(
        memory_set_id=MEMORY_SET, memory_type="interpretation", scope_type="topic",
        scope_key=action["memory_unit"]["scope_key"], subject_ref=action["memory_unit"]["subject_ref"],
        predicate="visual_daily_pattern")
    assert unit["memory_unit_id"] == original_ids[0]
    assert unit["qualifiers"]["digest_id"] == later["digest_id"]


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
        monkeypatch.setattr(runner.memory.action_resolver, "resolve_memory_actions_batch", Mock(side_effect=error))
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
        assert saved["support_review"]["decisions"][0]["support_refs"]
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
