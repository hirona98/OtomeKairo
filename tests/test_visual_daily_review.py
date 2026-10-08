from copy import deepcopy
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from otomekairo.llm.contracts import LLMError
from otomekairo.llm.visual_daily import validate_grouping, validate_support
from otomekairo.service.visual_daily import ServiceVisualDailyMixin


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
    runner.memory = SimpleNamespace(action_resolver=SimpleNamespace(resolve_memory_actions=Mock()))
    runner._now_iso = lambda: "2026-10-05T09:00:00+09:00"
    runner._visual_daily_repeated_support = Mock(side_effect=LLMError("semantic review unavailable"))
    digest = {"digest_id": "digest:test", "memory_set_id": "memory:test", "memory_candidate_summaries": [
        {"summary_text": "観測要約。", "source_key": "vision:test"}]}
    with pytest.raises(LLMError):
        runner._promote_visual_daily_digest_memory_candidates(digest=digest, state={})
    runner.memory.action_resolver.resolve_memory_actions.assert_not_called()
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
