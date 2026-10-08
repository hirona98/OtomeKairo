from otomekairo.memory.consolidator import MemoryConsolidator
from otomekairo.memory.correction import MemoryCorrectionReconciler
from types import SimpleNamespace
import pytest


def test_memory_review_preserves_separate_runs_origins_and_terminal_states():
    consolidator = MemoryConsolidator.__new__(MemoryConsolidator)
    events = [
        {"event_id": "event:post", "cycle_id": "cycle:post", "run_id": "run:post",
         "created_at": "2026-10-04T09:00:45+09:00", "source_kind": "autonomous_run",
         "kind": "autonomous_run_terminal", "role": "system", "terminal_status": "completed"},
        {"event_id": "event:reply", "cycle_id": "cycle:reply", "run_id": "run:reply",
         "created_at": "2026-10-04T09:00:46+09:00", "source_kind": "autonomous_run",
         "kind": "autonomous_run_terminal", "role": "system", "terminal_status": "cancelled"},
    ]
    context = consolidator._build_memory_interpretation_context(memory_context={}, events=events)
    assert context["events"] == events


def test_correction_candidate_keeps_the_completed_runs_identity_and_lifecycle():
    reconciler = MemoryCorrectionReconciler.__new__(MemoryCorrectionReconciler)
    qualifiers = {"autonomous_run_ids": ["run:post"], "autonomous_run_terminal_status": "completed"}
    unit = {"memory_unit_id": "memory:post", "memory_type": "commitment", "commitment_state": "done",
            "summary_text": "投稿結果を確認して報告する。", "qualifiers": qualifiers}
    target = {"memory_unit": unit, "operation": "refine", "revision": {
        "revision_id": "revision:post", "before_snapshot": {**unit, "commitment_state": "open", "qualifiers": {}},
        "after_snapshot": unit}}
    compact = reconciler.compact_target(target)
    assert compact["current_memory_unit"]["commitment_state"] == "done"
    assert compact["current_memory_unit"]["qualifiers"] == qualifiers
    assert compact["after_snapshot"]["qualifiers"] == qualifiers
    assert compact["before_snapshot"]["commitment_state"] == "open"
    assert compact["after_snapshot"]["commitment_state"] == "done"
    compact["current_memory_unit"]["qualifiers"]["autonomous_run_ids"].append("run:other")
    assert qualifiers["autonomous_run_ids"] == ["run:post"]


def test_correction_of_an_old_revision_uses_its_claim_before_later_completion():
    reconciler = MemoryCorrectionReconciler.__new__(MemoryCorrectionReconciler)
    promised = {"summary_text": "5分後に一度知らせると約束した。", "commitment_state": "open"}
    delivered = {"memory_unit_id": "memory:timer", "summary_text": "通知して作業を完了した。",
                 "commitment_state": "done", "qualifiers": {"autonomous_run_ids": ["run:timer"]}}
    target = {"memory_unit": delivered, "operation": "create", "occurred_at": "2026-10-04T09:00:47+09:00",
              "revision": {"revision_id": "revision:promise", "before_snapshot": None, "after_snapshot": promised}}
    compact = reconciler.compact_target(target)
    assert compact["occurred_at"] == target["occurred_at"]
    assert compact["before_snapshot"] is None
    assert compact["after_snapshot"] == promised
    assert compact["current_memory_unit"]["summary_text"] == delivered["summary_text"]
    assert "summary_text" not in compact


@pytest.mark.parametrize("missing", [False, True])
def test_correction_review_requires_its_revisions_primary_evidence(missing):
    speech = {"event_id": "event:reported", "kind": "speech", "role": "assistant",
              "run_id": "run:post", "text": "投稿成功を確認しました。" * 100,
              "created_at": "2026-10-04T09:00:45+09:00"}
    target = {"memory_unit": {}, "revision": {"evidence_event_ids": [speech["event_id"]]}}
    reconciler = MemoryCorrectionReconciler.__new__(MemoryCorrectionReconciler)
    reconciler.store = SimpleNamespace(list_recent_memory_revision_targets_for_correction=lambda **kw: [target],
        load_events_for_evidence=lambda **kw: [] if missing else [speech])
    args = dict(memory_set_id="memory:test", cycle_id="cycle:current",
                finished_at="2026-10-04T09:00:46+09:00", candidate_memory_unit_ids=[])
    if missing:
        with pytest.raises(ValueError, match="根拠イベント"):
            reconciler.prepare(**args)
    else:
        compact = reconciler.compact_target(reconciler.prepare(**args)["targets"][0])
        assert compact["source_evidence_events"] == [speech]
