from datetime import datetime, timezone, timedelta
import threading
from unittest.mock import Mock

import pytest

from otomekairo.llm.contracts import LLMError
from otomekairo.service.app import OtomeKairoService
from otomekairo.store.file_store import FileStore


MEMORY_SET = "memory_set:default"
STAMP = "2026-10-25T10:00:00+09:00"


@pytest.fixture
def runner(tmp_path, monkeypatch):
    instance = OtomeKairoService.__new__(OtomeKairoService)
    instance.store = FileStore(tmp_path)
    instance._runtime_state_lock = threading.RLock()
    instance._visual_daily_runtime_state = {"current_digest_id": None}
    instance._now_iso = lambda: STAMP
    instance.llm = Mock()
    instance.llm.generate_visual_daily_grouping.side_effect = lambda **kw: {
        "groups": [{"observation_ids": [record["visual_observation_id"]],
                    "summary_text": "検証用の場面。", "reason_summary": "観測根拠を整理した。"}
                   for record in kw["records"]],
    }
    monkeypatch.setattr("otomekairo.service.visual_daily.local_now", lambda: datetime(
        2026, 10, 25, 10, tzinfo=timezone(timedelta(hours=9))))

    def promote(*, digest, state):
        instance._store_visual_daily_promotion_result(
            digest=digest, result_status="skipped", promoted_memory_unit_ids=[],
            skipped_candidate_count=0, failure_reason=None, relation_index_sync=None,
        )

    instance._promote_visual_daily_digest_memory_candidates = Mock(side_effect=promote)
    return instance


def seed_record(runner, day, *, memory_set_id=MEMORY_SET):
    record = {
        "visual_observation_id": f"visual:{memory_set_id}:{day}", "memory_set_id": memory_set_id,
        "cycle_id": f"cycle:{day}", "observed_at": f"2026-10-{day:02d}T09:00:00+09:00",
        "source_kind": "camera", "vision_source_id": "vision_source:test",
        "image_input_kind": "vision_capture_result", "retention_status": "active",
        "detailed_summary_text": "検証用の観測。",
    }
    with runner.store._memory_db() as conn:
        runner.store._insert_visual_observation_record(conn, record)


def seed_digest(runner, day, *, status="succeeded", promotion="not_started", memory_set_id=MEMORY_SET):
    digest = runner._build_visual_daily_digest(
        digest_id=runner._visual_daily_digest_id(memory_set_id=memory_set_id, local_date=f"2026-10-{day:02d}"),
        memory_set_id=memory_set_id, local_date=f"2026-10-{day:02d}",
        started_at=STAMP, finished_at=STAMP, records=[], groups=[],
    )
    digest["result_status"] = status
    digest["memory_promotion"]["result_status"] = promotion
    runner.store.upsert_daily_visual_digest(digest=digest, updated_records=[])
    return digest


def test_digest_backlog_advances_through_all_batches(runner):
    for day in range(1, 17):
        seed_record(runner, day)
    for expected_count in (7, 14, 16, 16):
        runner._run_due_visual_daily_digests()
        assert len(runner.store.list_daily_visual_digests(memory_set_id=MEMORY_SET, limit=30)) == expected_count
    assert runner.llm.generate_visual_daily_grouping.call_count == 16
    assert [call.kwargs["digest"]["local_date"] for call in
            runner._promote_visual_daily_digest_memory_candidates.call_args_list] == [
        f"2026-10-{day:02d}" for day in range(1, 17)
    ]


def test_pending_dates_filter_processed_days_and_memory_set_before_limit(runner):
    for day in range(1, 17):
        seed_record(runner, day)
        if day <= 7:
            seed_digest(runner, day, status="failed" if day % 2 else "succeeded")
    seed_record(runner, 25)
    seed_record(runner, 26)
    seed_record(runner, 1, memory_set_id="memory_set:other")
    seed_digest(runner, 8, memory_set_id="memory_set:other")
    assert runner.store.list_pending_visual_daily_dates(
        memory_set_id=MEMORY_SET, before_local_date="2026-10-25", limit=7,
    ) == [f"2026-10-{day:02d}" for day in range(8, 15)]
    assert runner.store.list_pending_visual_daily_dates(
        memory_set_id="memory_set:other", before_local_date="2026-10-25", limit=7,
    ) == ["2026-10-01"]


def test_promotion_backlog_is_not_hidden_by_newer_terminal_digests(runner):
    for day in range(1, 17):
        seed_digest(runner, day)
    for day in range(17, 24):
        seed_digest(runner, day, promotion=("succeeded", "skipped", "failed")[day % 3])
    seed_digest(runner, 24, status="failed")
    seed_digest(runner, 1, memory_set_id="memory_set:other")
    for expected_count in (7, 14, 16, 16):
        runner._run_due_visual_daily_promotions(memory_set_id=MEMORY_SET)
        assert runner._promote_visual_daily_digest_memory_candidates.call_count == expected_count
    assert [call.kwargs["digest"]["local_date"] for call in
            runner._promote_visual_daily_digest_memory_candidates.call_args_list] == [
        f"2026-10-{day:02d}" for day in range(1, 17)
    ]
    assert runner.store.get_daily_visual_digest(
        memory_set_id=MEMORY_SET, local_date="2026-10-24",
    )["result_status"] == "failed"
    assert runner.store.get_daily_visual_digest(
        memory_set_id="memory_set:other", local_date="2026-10-01",
    )["memory_promotion"]["result_status"] == "not_started"


def test_failed_grouping_is_kept_while_next_poll_processes_remaining_dates(runner):
    seed_record(runner, 1)
    seed_record(runner, 2)
    generate = runner.llm.generate_visual_daily_grouping.side_effect
    runner.llm.generate_visual_daily_grouping.side_effect = LLMError("検証用の生成失敗。")
    with pytest.raises(LLMError):
        runner._run_due_visual_daily_digests()
    failed = runner.store.get_daily_visual_digest(memory_set_id=MEMORY_SET, local_date="2026-10-01")
    assert failed["result_status"] == "failed"
    runner.llm.generate_visual_daily_grouping.side_effect = generate
    runner._run_due_visual_daily_digests()
    assert runner.store.get_daily_visual_digest(memory_set_id=MEMORY_SET, local_date="2026-10-01") == failed
    assert runner.store.get_daily_visual_digest(
        memory_set_id=MEMORY_SET, local_date="2026-10-02",
    )["result_status"] == "succeeded"
    assert runner.llm.generate_visual_daily_grouping.call_count == 2
    assert runner._promote_visual_daily_digest_memory_candidates.call_count == 1
