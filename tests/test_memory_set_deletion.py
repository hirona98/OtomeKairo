from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from otomekairo.service.app import OtomeKairoService
from otomekairo.service.common import ServiceError
from otomekairo.store.clone import MemorySetInUseError
from otomekairo.store.file_store import FileStore, SQLiteMemoryStore


MEMORY_SET = "memory_set:inactive"
STAMP = "2026-10-26T12:00:00+09:00"


@pytest.fixture
def service(tmp_path):
    instance = OtomeKairoService.__new__(OtomeKairoService)
    instance.store = FileStore(tmp_path)
    instance._config_update_lock = threading.RLock()
    instance._memory_set_work_counts = {}
    instance._runtime_state_lock = threading.RLock()
    instance._memory_postprocess_runtime_state = {"current_cycle_id": None}
    instance._now_iso = lambda: STAMP
    instance.memory = SimpleNamespace(run_postprocess_job=Mock())
    token = instance.acquire_console_access_token()["console_access_token"]
    state = instance.store.read_state()
    state["memory_sets"][MEMORY_SET] = {
        **deepcopy(state["memory_sets"][state["selected_memory_set_id"]]),
        "memory_set_id": MEMORY_SET,
    }
    instance.store.write_state(state)
    job = {"cycle_id": "cycle:test", "memory_set_id": MEMORY_SET,
           "queued_at": STAMP, "result_status": "queued"}
    instance.store.upsert_memory_postprocess_job(job=job)
    return instance, token, job


@pytest.mark.parametrize("fails", [False, True])
def test_delete_rejects_running_job_and_succeeds_after_it_finishes(service, fails):
    instance, token, job = service
    entered, release = threading.Event(), threading.Event()

    def process(*, job):
        entered.set()
        assert release.wait(3)
        if fails:
            raise ValueError("検証用の後処理失敗。")
        return {key: {"result_status": "succeeded"} for key in (
            "vector_index_sync", "relation_index_sync", "correction_reconciliation",
            "reflective_consolidation",
        )}

    instance.memory.run_postprocess_job.side_effect = process
    before = instance.store.read_state()
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(instance._run_memory_postprocess_job, job)
        try:
            assert entered.wait(3)
            with pytest.raises(ServiceError) as caught:
                instance.delete_memory_set(token, MEMORY_SET)
            assert caught.value.status_code == 409
            assert caught.value.error_code == "memory_set_in_use"
            assert instance.store.read_state() == before
            assert instance.store.get_memory_postprocess_job(job["cycle_id"])["result_status"] == "running"
        finally:
            release.set()
        running.result(timeout=3)

    completed = instance.store.get_memory_postprocess_job(job["cycle_id"])
    assert completed["result_status"] == ("failed" if fails else "succeeded")
    assert instance.delete_memory_set(token, MEMORY_SET) == {"deleted_memory_set_id": MEMORY_SET}
    instance._run_memory_postprocess_job(job)
    assert instance.store.get_memory_postprocess_job(job["cycle_id"]) is None
    assert MEMORY_SET not in instance.store.read_state()["memory_sets"]
    assert instance.memory.run_postprocess_job.call_count == 1
    with instance.store._memory_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM events WHERE memory_set_id = ?", (MEMORY_SET,)).fetchone()[0] == 0


def test_deleted_queued_job_is_not_started_from_an_old_queue_entry(service):
    instance, token, job = service
    instance.delete_memory_set(token, MEMORY_SET)
    instance._run_memory_postprocess_job(job)
    instance.memory.run_postprocess_job.assert_not_called()
    assert instance.store.get_memory_postprocess_job(job["cycle_id"]) is None


def test_claim_and_delete_are_serialized_across_store_connections(tmp_path, monkeypatch):
    worker_store = SQLiteMemoryStore(tmp_path)
    deleting_store = SQLiteMemoryStore(tmp_path)
    job = {"cycle_id": "cycle:test", "memory_set_id": MEMORY_SET,
           "queued_at": STAMP, "result_status": "queued"}
    worker_store.upsert_memory_postprocess_job(job=job)
    claimed, release, attempted = threading.Event(), threading.Event(), threading.Event()
    original_insert = worker_store._insert_memory_postprocess_job

    def held_insert(conn, record):
        claimed.set()
        assert release.wait(3)
        original_insert(conn, record)

    monkeypatch.setattr(worker_store, "_insert_memory_postprocess_job", held_insert)

    def delete():
        attempted.set()
        deleting_store.delete_memory_set_records(MEMORY_SET)

    with ThreadPoolExecutor(max_workers=2) as pool:
        claiming = pool.submit(worker_store.claim_memory_postprocess_job,
                               cycle_id=job["cycle_id"], started_at=STAMP)
        deleting = None
        try:
            assert claimed.wait(3)
            deleting = pool.submit(delete)
            assert attempted.wait(3)
            assert not deleting.done()
        finally:
            release.set()
        assert claiming.result(timeout=3)["result_status"] == "running"
        with pytest.raises(MemorySetInUseError):
            deleting.result(timeout=3)
    assert worker_store.get_memory_postprocess_job(job["cycle_id"])["result_status"] == "running"


def test_only_one_worker_can_claim_a_queued_job(tmp_path):
    stores = [SQLiteMemoryStore(tmp_path), SQLiteMemoryStore(tmp_path)]
    job = {"cycle_id": "cycle:test", "memory_set_id": MEMORY_SET,
           "queued_at": STAMP, "result_status": "queued"}
    stores[0].upsert_memory_postprocess_job(job=job)
    barrier = threading.Barrier(2)

    def claim(store):
        barrier.wait(timeout=3)
        return store.claim_memory_postprocess_job(cycle_id=job["cycle_id"], started_at=STAMP)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, stores))
    assert sum(result is not None for result in results) == 1


@pytest.mark.parametrize("status", ["succeeded", "failed"])
def test_terminal_jobs_are_not_started_again(service, status):
    instance, _, job = service
    instance.store.upsert_memory_postprocess_job(job={**job, "result_status": status})
    instance._run_memory_postprocess_job(job)
    instance.memory.run_postprocess_job.assert_not_called()
    assert instance.store.get_memory_postprocess_job(job["cycle_id"])["result_status"] == status


@pytest.mark.parametrize("stage", ["grouping", "digest_promotion", "queued_promotion"])
@pytest.mark.parametrize("fails", [False, True])
def test_visual_work_keeps_its_memory_set_until_all_writes_finish(service, monkeypatch, stage, fails):
    instance, token, _ = service
    instance._visual_daily_runtime_state = {"current_digest_id": None}
    record = {
        "visual_observation_id": "visual_observation:test", "memory_set_id": MEMORY_SET,
        "cycle_id": "cycle:visual", "observed_at": STAMP, "source_kind": "camera",
        "vision_source_id": "vision_source:test", "image_input_kind": "vision_capture_result",
        "retention_status": "active", "detailed_summary_text": "検証用の観測。",
    }
    with instance.store._memory_db() as conn:
        instance.store._insert_visual_observation_record(conn, record)
    # 意味生成を固定し、実DBへの保存と削除の競合を検証する。
    monkeypatch.setattr(instance, "_visual_daily_groups", lambda **kwargs: [])
    if stage == "queued_promotion":
        digest = instance._build_visual_daily_digest(
            digest_id="daily_visual_digest:test", memory_set_id=MEMORY_SET,
            local_date="2026-10-26", started_at=STAMP, finished_at=STAMP, records=[record], groups=[],
        )
        instance.store.upsert_daily_visual_digest(digest=digest, updated_records=[])
    method_name = "_visual_daily_groups" if stage == "grouping" else "_promote_visual_daily_digest_memory_candidates"
    original = getattr(instance, method_name)
    entered, release = threading.Event(), threading.Event()

    def held_work(**kwargs):
        entered.set()
        assert release.wait(3)
        if fails:
            raise ValueError("検証用の視覚処理失敗。")
        return original(**kwargs)

    monkeypatch.setattr(instance, method_name, held_work)

    def run():
        if stage == "queued_promotion":
            return instance._run_due_visual_daily_promotions(memory_set_id=MEMORY_SET)
        return instance._run_visual_daily_digest(memory_set_id=MEMORY_SET, local_date="2026-10-26")

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(run)
        try:
            assert entered.wait(3)
            with pytest.raises(ServiceError) as caught:
                instance.delete_memory_set(token, MEMORY_SET)
            assert caught.value.error_code == "memory_set_in_use"
            assert MEMORY_SET in instance.store.read_state()["memory_sets"]
            # LLM処理中も設定更新や、他の集合の削除を妨げない。
            instance.patch_current(token, {"thinking_speech_level": 3})
            state = instance.store.read_state()
            other = "memory_set:other"
            state["memory_sets"][other] = {**state["memory_sets"][MEMORY_SET], "memory_set_id": other}
            instance.store.write_state(state)
            instance.delete_memory_set(token, other)
        finally:
            release.set()
        if fails:
            with pytest.raises(ValueError, match="検証用"):
                pending.result(timeout=3)
        else:
            pending.result(timeout=3)

    instance.delete_memory_set(token, MEMORY_SET)
    with instance.store._memory_db() as conn:
        for table in ("visual_observation_records", "daily_visual_digests"):
            assert conn.execute(f"SELECT COUNT(*) FROM {table} WHERE memory_set_id = ?", (MEMORY_SET,)).fetchone()[0] == 0


@pytest.mark.parametrize("stage", ["grouping", "promotion"])
def test_deleted_memory_set_cannot_restart_visual_work(service, monkeypatch, stage):
    instance, token, _ = service
    instance.delete_memory_set(token, MEMORY_SET)
    groups = Mock()
    promote = Mock()
    monkeypatch.setattr(instance, "_visual_daily_groups", groups)
    monkeypatch.setattr(instance, "_promote_visual_daily_digest_memory_candidates", promote)
    with pytest.raises(ServiceError) as caught:
        if stage == "grouping":
            instance._run_visual_daily_digest(memory_set_id=MEMORY_SET, local_date="2026-10-26")
        else:
            instance._run_due_visual_daily_promotions(memory_set_id=MEMORY_SET)
    assert caught.value.error_code == "memory_set_not_found"
    groups.assert_not_called()
    promote.assert_not_called()
