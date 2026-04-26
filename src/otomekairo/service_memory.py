from __future__ import annotations

import queue
import threading
import uuid
from typing import Any

from otomekairo.service_common import debug_log


class ServiceMemoryMixin:
    def start_background_memory_postprocess_worker(self) -> None:
        # 既存
        with self._runtime_state_lock:
            if (
                self._background_memory_postprocess_thread is not None
                and self._background_memory_postprocess_thread.is_alive()
            ):
                debug_log("MemoryWorker", "already running")
                return

            # 再起動時も incomplete job を拾い直せるよう、永続状態からキューを復元する。
            self._memory_postprocess_queue = queue.Queue()
            restored_jobs = self.store.list_memory_postprocess_jobs(
                result_statuses=["queued", "running"],
            )
            debug_log("MemoryWorker", f"restoring jobs count={len(restored_jobs)}")
            for job in restored_jobs:
                requeued_job = self._requeue_memory_postprocess_job(job)
                self._memory_postprocess_queue.put(requeued_job)

            stop_event = threading.Event()
            thread = threading.Thread(
                target=self._background_memory_postprocess_loop,
                args=(stop_event,),
                name="otomekairo-background-memory-postprocess",
                daemon=True,
            )
            self._background_memory_postprocess_stop_event = stop_event
            self._background_memory_postprocess_thread = thread
            self._memory_postprocess_runtime_state["current_cycle_id"] = None

        # 開始
        thread.start()
        debug_log("MemoryWorker", f"started thread={thread.name}")

    def stop_background_memory_postprocess_worker(self) -> None:
        # スナップショット
        with self._runtime_state_lock:
            stop_event = self._background_memory_postprocess_stop_event
            thread = self._background_memory_postprocess_thread
            self._background_memory_postprocess_stop_event = None
            self._background_memory_postprocess_thread = None
            self._memory_postprocess_runtime_state["current_cycle_id"] = None

        # 停止
        if stop_event is not None:
            stop_event.set()
        self._memory_postprocess_queue.put(None)
        if thread is not None and thread.is_alive():
            thread.join(timeout=5.0)
        debug_log("MemoryWorker", "stopped")

    def _requeue_memory_postprocess_job(self, job: dict[str, Any]) -> dict[str, Any]:
        # 再投入
        requeued_job = {
            **job,
            "started_at": None,
            "finished_at": None,
            "result_status": "queued",
        }
        self.store.upsert_memory_postprocess_job(job=requeued_job)
        self._update_memory_trace_postprocess(
            cycle_id=requeued_job["cycle_id"],
            vector_index_sync={
                "result_status": "queued",
                "failure_reason": None,
            },
            reflective_consolidation={
                "started": False,
                "result_status": "queued",
                "trigger_reasons": [],
                "affected_memory_unit_ids": [],
                "summary_generation": {
                    "requested_scope_count": 0,
                    "succeeded_scope_count": 0,
                    "failed_scopes": [],
                },
                "drive_state_update": self._drive_state_update_trace("queued"),
                "failure_reason": None,
            },
            emit_logs=False,
        )
        debug_log("MemoryWorker", f"requeued cycle={self._short_cycle_id(requeued_job['cycle_id'])}")
        return requeued_job

    def _background_memory_postprocess_loop(self, stop_event: threading.Event) -> None:
        # ループ
        debug_log("MemoryWorker", "loop started")
        while True:
            if stop_event.is_set() and self._memory_postprocess_queue.empty():
                break

            try:
                job = self._memory_postprocess_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            if job is None:
                if stop_event.is_set():
                    break
                continue

            self._run_memory_postprocess_job(job)
        debug_log("MemoryWorker", "loop stopped")

    def _run_memory_postprocess_job(self, job: dict[str, Any]) -> None:
        # 削除済み job は走らせない。
        persisted_job = self.store.get_memory_postprocess_job(job["cycle_id"])
        if persisted_job is None:
            debug_log("MemoryWorker", f"skip missing cycle={self._short_cycle_id(job['cycle_id'])}")
            return

        # job開始
        started_job = {
            **persisted_job,
            "started_at": self._now_iso(),
            "finished_at": None,
            "result_status": "running",
        }
        self.store.upsert_memory_postprocess_job(job=started_job)
        with self._runtime_state_lock:
            self._memory_postprocess_runtime_state["current_cycle_id"] = started_job["cycle_id"]
        debug_log(
            "MemoryWorker",
            (
                f"job start cycle={self._short_cycle_id(started_job['cycle_id'])} "
                f"memory_set={self._short_identifier(started_job['memory_set_id'])}"
            ),
        )

        try:
            # 実行
            postprocess_result = self.memory.run_postprocess_job(job=started_job)
            self._update_memory_trace_postprocess(
                cycle_id=started_job["cycle_id"],
                vector_index_sync=postprocess_result["vector_index_sync"],
                reflective_consolidation=postprocess_result["reflective_consolidation"],
            )
            self._append_vector_index_failure_events(
                cycle_id=started_job["cycle_id"],
                memory_set_id=started_job["memory_set_id"],
                vector_index_sync=postprocess_result["vector_index_sync"],
            )

            # 完了
            completed_job = {
                **started_job,
                "finished_at": self._now_iso(),
                "result_status": (
                    "failed"
                    if (
                        postprocess_result["vector_index_sync"]["result_status"] == "failed"
                        or postprocess_result["reflective_consolidation"]["result_status"] == "failed"
                    )
                    else "succeeded"
                ),
            }
            self.store.upsert_memory_postprocess_job(job=completed_job)
            debug_log(
                "MemoryWorker",
                (
                    f"job done cycle={self._short_cycle_id(started_job['cycle_id'])} "
                    f"status={completed_job['result_status']} "
                    f"vector={postprocess_result['vector_index_sync']['result_status']} "
                    f"reflection={postprocess_result['reflective_consolidation']['result_status']}"
                ),
            )
        except Exception as exc:  # noqa: BLE001
            # 想定外失敗
            failure_reason = str(exc)
            debug_log(
                "MemoryWorker",
                f"job failed cycle={self._short_cycle_id(started_job['cycle_id'])} error={type(exc).__name__}: {failure_reason}",
            )
            self._update_memory_trace_postprocess(
                cycle_id=started_job["cycle_id"],
                vector_index_sync={
                    "result_status": "failed",
                    "failure_reason": failure_reason,
                },
                reflective_consolidation={
                    "started": False,
                    "result_status": "not_started",
                    "trigger_reasons": [],
                    "affected_memory_unit_ids": [],
                    "summary_generation": {
                        "requested_scope_count": 0,
                        "succeeded_scope_count": 0,
                        "failed_scopes": [],
                    },
                    "drive_state_update": self._drive_state_update_trace("not_started"),
                    "failure_reason": None,
                },
            )
            self._append_vector_index_failure_events(
                cycle_id=started_job["cycle_id"],
                memory_set_id=started_job["memory_set_id"],
                vector_index_sync={
                    "result_status": "failed",
                    "failure_reason": failure_reason,
                },
            )
            self.store.upsert_memory_postprocess_job(
                job={
                    **started_job,
                    "finished_at": self._now_iso(),
                    "result_status": "failed",
                }
            )
        finally:
            with self._runtime_state_lock:
                self._memory_postprocess_runtime_state["current_cycle_id"] = None

    def _queue_memory_postprocess_job(self, job: dict[str, Any]) -> None:
        # 永続化してから in-memory queue に載せる。
        self.store.upsert_memory_postprocess_job(job=job)
        self._memory_postprocess_queue.put(job)
        debug_log(
            "MemoryWorker",
            (
                f"queued cycle={self._short_cycle_id(job['cycle_id'])} "
                f"memory_set={self._short_identifier(job['memory_set_id'])}"
            ),
        )

    def _update_memory_trace_postprocess(
        self,
        *,
        cycle_id: str,
        vector_index_sync: dict[str, Any],
        reflective_consolidation: dict[str, Any],
        emit_logs: bool = True,
    ) -> None:
        # 検索
        cycle_trace = self.store.get_cycle_trace(cycle_id)
        if cycle_trace is None:
            return

        # 更新
        memory_trace = cycle_trace.get("memory_trace")
        if not isinstance(memory_trace, dict):
            memory_trace = self._pending_memory_trace()
        memory_trace["vector_index_sync"] = vector_index_sync
        memory_trace["reflective_consolidation"] = reflective_consolidation
        memory_trace["drive_state_update"] = reflective_consolidation.get(
            "drive_state_update",
            self._drive_state_update_trace("not_started"),
        )
        self.store.replace_cycle_trace(
            cycle_trace={
                **cycle_trace,
                "memory_trace": memory_trace,
            }
        )

        # 監査 / ログ
        self._append_reflective_failure_events(
            cycle_id=cycle_id,
            memory_set_id=cycle_trace["cycle_summary"]["selected_memory_set_id"],
            memory_trace=memory_trace,
        )
        self._append_reflective_summary_generation_failure_events(
            cycle_id=cycle_id,
            memory_set_id=cycle_trace["cycle_summary"]["selected_memory_set_id"],
            memory_trace=memory_trace,
        )
        if emit_logs:
            self._emit_memory_trace_logs(cycle_id=cycle_id, memory_trace=memory_trace)

    def _finalize_memory_trace(
        self,
        *,
        cycle_id: str,
        finished_at: str,
        state: dict[str, Any],
        input_text: str,
        events: list[dict[str, Any]],
        pipeline: dict[str, Any],
    ) -> None:
        # ターン統合
        debug_log("Memory", f"turn consolidation start cycle={self._short_cycle_id(cycle_id)}")
        try:
            memory_trace, postprocess_job = self.memory.consolidate_turn(
                state=state,
                cycle_id=cycle_id,
                finished_at=finished_at,
                input_text=input_text,
                recall_hint=pipeline["recall_hint"],
                decision=pipeline["decision"],
                reply_payload=pipeline["reply_payload"],
                events=events,
            )
        except Exception as exc:  # noqa: BLE001
            debug_log(
                "Memory",
                f"turn consolidation failed cycle={self._short_cycle_id(cycle_id)} error={type(exc).__name__}: {exc}",
            )
            memory_trace = self._failed_memory_trace(str(exc))
            self.store.append_events(
                events=[
                    self._build_memory_audit_event(
                        cycle_id=cycle_id,
                        memory_set_id=state["selected_memory_set_id"],
                        kind="memory_consolidation_failure",
                        created_at=self._now_iso(),
                        payload={"failure_reason": str(exc)},
                    )
                ]
            )
            postprocess_job = None

        # memory trace更新
        self._update_cycle_trace_memory_trace(cycle_id=cycle_id, memory_trace=memory_trace)

        # デバッグログ群
        self._emit_memory_trace_logs(cycle_id=cycle_id, memory_trace=memory_trace)

        # 後段job投入
        if postprocess_job is None:
            debug_log("Memory", f"turn consolidation done cycle={self._short_cycle_id(cycle_id)} postprocess=none")
            return
        try:
            self._queue_memory_postprocess_job(postprocess_job)
            debug_log("Memory", f"turn consolidation done cycle={self._short_cycle_id(cycle_id)} postprocess=queued")
        except Exception as exc:  # noqa: BLE001
            debug_log(
                "Memory",
                f"postprocess queue failed cycle={self._short_cycle_id(cycle_id)} error={type(exc).__name__}: {exc}",
            )
            failed_postprocess_trace = {
                **memory_trace,
                "vector_index_sync": {
                    "result_status": "failed",
                    "failure_reason": str(exc),
                },
                "reflective_consolidation": {
                    "started": False,
                    "result_status": "not_started",
                    "trigger_reasons": [],
                    "affected_memory_unit_ids": [],
                    "summary_generation": {
                        "requested_scope_count": 0,
                        "succeeded_scope_count": 0,
                        "failed_scopes": [],
                    },
                    "failure_reason": None,
                },
            }
            self._update_cycle_trace_memory_trace(
                cycle_id=cycle_id,
                memory_trace=failed_postprocess_trace,
            )
            self._append_vector_index_failure_events(
                cycle_id=cycle_id,
                memory_set_id=state["selected_memory_set_id"],
                vector_index_sync=failed_postprocess_trace["vector_index_sync"],
            )
            self._emit_memory_trace_logs(
                cycle_id=cycle_id,
                memory_trace=failed_postprocess_trace,
            )

    def _failed_memory_trace(self, failure_reason: str) -> dict[str, Any]:
        # 結果
        return {
            "turn_consolidation_status": "failed",
            "episode_id": None,
            "episode_summary": None,
            "episode_series_id": None,
            "open_loops": [],
            "memory_action_count": 0,
            "episode_affect_count": 0,
            "updated_memory_unit_ids": [],
            "episode_affects": [],
            "mood_state_update": None,
            "affect_state_updates": [],
            "failure_reason": failure_reason,
            "vector_index_sync": {
                "result_status": "not_started",
                "failure_reason": None,
            },
            "reflective_consolidation": {
                "started": False,
                "result_status": "not_started",
                "trigger_reasons": [],
                "affected_memory_unit_ids": [],
                "summary_generation": {
                    "requested_scope_count": 0,
                    "succeeded_scope_count": 0,
                    "failed_scopes": [],
                },
                "drive_state_update": self._drive_state_update_trace("not_started"),
                "failure_reason": None,
            },
            "drive_state_update": self._drive_state_update_trace("not_started"),
        }

    def _skipped_memory_trace(self, reason: str) -> dict[str, Any]:
        # 結果
        return {
            "turn_consolidation_status": "skipped",
            "episode_id": None,
            "episode_summary": None,
            "episode_series_id": None,
            "open_loops": [],
            "memory_action_count": 0,
            "episode_affect_count": 0,
            "updated_memory_unit_ids": [],
            "episode_affects": [],
            "mood_state_update": None,
            "affect_state_updates": [],
            "failure_reason": None,
            "skip_reason": reason,
            "vector_index_sync": {
                "result_status": "skipped",
                "failure_reason": None,
            },
            "reflective_consolidation": {
                "started": False,
                "result_status": "skipped",
                "trigger_reasons": [],
                "affected_memory_unit_ids": [],
                "summary_generation": {
                    "requested_scope_count": 0,
                    "succeeded_scope_count": 0,
                    "failed_scopes": [],
                },
                "drive_state_update": self._drive_state_update_trace("skipped"),
                "failure_reason": None,
            },
            "drive_state_update": self._drive_state_update_trace("skipped"),
        }

    def _append_vector_index_failure_events(
        self,
        *,
        cycle_id: str,
        memory_set_id: str,
        vector_index_sync: dict[str, Any],
    ) -> None:
        # 検索
        if vector_index_sync.get("result_status") != "failed":
            return

        # 監査
        self.store.append_events(
            events=[
                self._build_memory_audit_event(
                    cycle_id=cycle_id,
                    memory_set_id=memory_set_id,
                    kind="vector_index_sync_failure",
                    created_at=self._now_iso(),
                    payload={
                        "failure_reason": vector_index_sync.get("failure_reason"),
                    },
                )
            ]
        )

    def _append_reflective_failure_events(
        self,
        *,
        cycle_id: str,
        memory_set_id: str,
        memory_trace: dict[str, Any],
    ) -> None:
        # 検索
        reflective_trace = memory_trace.get("reflective_consolidation", {})
        if reflective_trace.get("result_status") != "failed":
            return

        # 監査
        self.store.append_events(
            events=[
                self._build_memory_audit_event(
                    cycle_id=cycle_id,
                    memory_set_id=memory_set_id,
                    kind="reflective_consolidation_failure",
                    created_at=self._now_iso(),
                    payload={
                        "failure_reason": reflective_trace.get("failure_reason"),
                        "trigger_reasons": reflective_trace.get("trigger_reasons", []),
                    },
                )
            ]
        )

    def _append_reflective_summary_generation_failure_events(
        self,
        *,
        cycle_id: str,
        memory_set_id: str,
        memory_trace: dict[str, Any],
    ) -> None:
        # 検索
        reflective_trace = memory_trace.get("reflective_consolidation", {})
        summary_generation = reflective_trace.get("summary_generation", {})
        failed_scopes = summary_generation.get("failed_scopes", [])
        if not isinstance(failed_scopes, list) or not failed_scopes:
            return

        # 監査
        created_at = self._now_iso()
        self.store.append_events(
            events=[
                self._build_memory_audit_event(
                    cycle_id=cycle_id,
                    memory_set_id=memory_set_id,
                    kind="reflective_summary_generation_failure",
                    created_at=created_at,
                    payload={
                        "scope_type": failure.get("scope_type"),
                        "scope_key": failure.get("scope_key"),
                        "failure_stage": failure.get("failure_stage"),
                        "failure_reason": failure.get("failure_reason"),
                    },
                )
                for failure in failed_scopes
                if isinstance(failure, dict)
            ]
        )

    def _pending_memory_trace(self) -> dict[str, Any]:
        return {
            "turn_consolidation_status": "pending",
            "episode_id": None,
            "episode_summary": None,
            "episode_series_id": None,
            "open_loops": [],
            "memory_action_count": 0,
            "episode_affect_count": 0,
            "updated_memory_unit_ids": [],
            "episode_affects": [],
            "mood_state_update": None,
            "affect_state_updates": [],
            "failure_reason": None,
            "vector_index_sync": {
                "result_status": "not_started",
                "failure_reason": None,
            },
            "reflective_consolidation": {
                "started": False,
                "result_status": "not_started",
                "trigger_reasons": [],
                "affected_memory_unit_ids": [],
                "summary_generation": {
                    "requested_scope_count": 0,
                    "succeeded_scope_count": 0,
                    "failed_scopes": [],
                },
                "drive_state_update": self._drive_state_update_trace("not_started"),
                "failure_reason": None,
            },
            "drive_state_update": self._drive_state_update_trace("not_started"),
        }

    def _drive_state_update_trace(self, result_status: str) -> dict[str, Any]:
        return {
            "result_status": result_status,
            "active_drive_ids": [],
            "removed_drive_ids": [],
            "drive_summaries": [],
        }

    def _update_cycle_trace_memory_trace(self, *, cycle_id: str, memory_trace: dict[str, Any]) -> None:
        # 検索
        cycle_trace = self.store.get_cycle_trace(cycle_id)
        if cycle_trace is None:
            return

        # 置換
        cycle_trace["memory_trace"] = memory_trace
        self.store.replace_cycle_trace(cycle_trace=cycle_trace)

    def _build_memory_audit_event(
        self,
        *,
        cycle_id: str,
        memory_set_id: str,
        kind: str,
        created_at: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        # イベント
        return {
            "event_id": f"event:{uuid.uuid4().hex}",
            "cycle_id": cycle_id,
            "memory_set_id": memory_set_id,
            "kind": kind,
            "role": "system",
            "created_at": created_at,
            **payload,
        }

    def _build_event_evidence_audit_events(
        self,
        *,
        cycle_id: str,
        memory_set_id: str,
        created_at: str,
        recall_pack: dict[str, Any],
    ) -> list[dict[str, Any]]:
        generation = recall_pack.get("event_evidence_generation", {})
        failed_items = generation.get("failed_items", []) if isinstance(generation, dict) else []
        events: list[dict[str, Any]] = []
        for failed_item in failed_items:
            if not isinstance(failed_item, dict):
                continue
            events.append(
                self._build_memory_audit_event(
                    cycle_id=cycle_id,
                    memory_set_id=memory_set_id,
                    kind="event_evidence_generation_failure",
                    created_at=created_at,
                    payload={
                        "source_event_id": failed_item.get("event_id"),
                        "source_event_kind": failed_item.get("kind"),
                        "failure_stage": failed_item.get("failure_stage"),
                        "failure_reason": failed_item.get("failure_reason"),
                    },
                )
            )
        return events
