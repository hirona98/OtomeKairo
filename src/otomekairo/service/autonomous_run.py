from __future__ import annotations

import threading
import uuid
from copy import deepcopy
from datetime import timedelta
from typing import Any

from otomekairo.llm.client import LLMError
from otomekairo.llm.contexts import AutonomousStepContext, CurrentInput
from otomekairo.llm.contracts import build_decision_target_stances_for_kind
from otomekairo.interaction import normalize_interaction_context
from otomekairo.service.capability import (
    CapabilityDispatchError,
    PreSendCheckFailureError,
    PreSendCheckWithheldError,
)
from otomekairo.service.common import ServiceError, debug_log


AUTONOMOUS_RUN_POLL_SECONDS = 1.0
AUTONOMOUS_RUN_CONTINUE_DELAY_SECONDS = 1
AUTONOMOUS_RUN_IDLE_CONTINUE_DELAY_SECONDS = 5
AUTONOMOUS_RUN_ACTIVE_STATUSES = {"active", "waiting_timer", "waiting_result", "paused"}
AUTONOMOUS_RUN_TERMINAL_STATUSES = {"completed", "cancelled"}
AUTONOMOUS_PRE_SEND_CHECK_RETRY_FEEDBACK = (
    "前の MCP request は送信前チェックで見送られた。"
    "同じ目的の安全な capability_request または action.kind=none を選ぶ。"
)
AUTONOMOUS_PRE_SEND_CHECK_WITHHELD_NOTICE = (
    "外部送信候補に非公開情報が含まれる可能性があるため、送信しませんでした。"
)
AUTONOMOUS_PRE_SEND_CHECK_FAILURE_NOTICE = (
    "外部送信内容の安全確認を完了できなかったため、送信しませんでした。"
)


class ServiceAutonomousRunMixin:
    def recover_autonomous_run_runtime_state_after_startup(self) -> None:
        # capability request の照合表は process-local なので、再起動後の result 待ちは再評価へ戻す。
        current_time = self._now_iso()
        state = self.store.read_state()
        memory_sets = state.get("memory_sets")
        if not isinstance(memory_sets, dict):
            return

        recovered_run_ids: list[str] = []
        for memory_set_id in memory_sets:
            if not isinstance(memory_set_id, str) or not memory_set_id.strip():
                continue
            runs = self.store.list_autonomous_runs(
                memory_set_id=memory_set_id,
                statuses=["waiting_result", "paused"],
                limit=200,
            )
            for run in runs:
                waiting_request_id = run.get("waiting_request_id")
                if not isinstance(waiting_request_id, str) or not waiting_request_id.strip():
                    continue
                request_record = self._autonomous_run_orphaned_request_record(run)
                updated = self._mark_autonomous_run_capability_wait_interrupted(
                    request_record=request_record,
                    current_time=current_time,
                    reason_code="orphaned_after_startup",
                    reason_summary=(
                        "process startup により capability result 照合表が失われたため、"
                        "autonomous_run の result 待ちを再評価へ戻した。"
                    ),
                )
                if isinstance(updated, dict):
                    recovered_run_ids.append(str(updated.get("run_id") or ""))

        recovered_run_ids = [run_id for run_id in recovered_run_ids if run_id]
        if recovered_run_ids:
            debug_log(
                "AutonomousRun",
                f"startup recovered orphaned waiting runs count={len(recovered_run_ids)}",
            )

    def start_background_autonomous_run_scheduler(self) -> None:
        # due autonomous_run は background_thinking とは別 worker で監視する。
        with self._runtime_state_lock:
            if (
                self._background_autonomous_run_thread is not None
                and self._background_autonomous_run_thread.is_alive()
            ):
                debug_log("AutonomousRun", "scheduler already running")
                return

            stop_event = threading.Event()
            thread = threading.Thread(
                target=self._background_autonomous_run_loop,
                args=(stop_event,),
                name="otomekairo-autonomous-run",
                daemon=True,
            )
            self._background_autonomous_run_stop_event = stop_event
            self._background_autonomous_run_thread = thread

        thread.start()

    def stop_background_autonomous_run_scheduler(self) -> None:
        # スナップショット
        with self._runtime_state_lock:
            stop_event = self._background_autonomous_run_stop_event
            thread = self._background_autonomous_run_thread
            self._background_autonomous_run_stop_event = None
            self._background_autonomous_run_thread = None

        # 停止
        if stop_event is not None:
            stop_event.set()
        if thread is not None and thread.is_alive():
            thread.join(timeout=5.0)
        debug_log("AutonomousRun", "scheduler stopped")

    def list_autonomous_runs_api(self, token: str | None) -> dict[str, Any]:
        # 認可
        state = self._require_token(token)
        current_time = self._now_iso()
        runs = self.store.list_autonomous_runs(
            memory_set_id=state["selected_memory_set_id"],
            limit=50,
        )
        return {
            "generated_at": current_time,
            "autonomous_runs": [
                self._autonomous_run_public_summary(run, current_time=current_time)
                for run in runs
            ],
        }

    def pause_autonomous_run_api(self, token: str | None, run_id: str) -> dict[str, Any]:
        # 認可
        state = self._require_token(token)
        run = self._require_autonomous_run(run_id)
        self._require_autonomous_run_memory_set(
            run=run,
            memory_set_id=str(state["selected_memory_set_id"]),
        )
        current_time = self._now_iso()
        if run.get("status") not in AUTONOMOUS_RUN_TERMINAL_STATUSES:
            run = self._paused_autonomous_run(
                run=run,
                current_time=current_time,
                pause_reason="manual_pause",
            )
            self.store.upsert_autonomous_run(autonomous_run=run)
        return {"autonomous_run": self._autonomous_run_public_summary(run, current_time=current_time)}

    def resume_autonomous_run_api(self, token: str | None, run_id: str) -> dict[str, Any]:
        # 認可
        state = self._require_token(token)
        run = self._require_autonomous_run(run_id)
        self._require_autonomous_run_memory_set(
            run=run,
            memory_set_id=str(state["selected_memory_set_id"]),
        )
        current_time = self._now_iso()
        if run.get("status") == "paused":
            run = {
                **run,
                "status": "active",
                "pause_reason": None,
                "resume_status": None,
                "next_run_at": current_time,
                "updated_at": current_time,
            }
            self.store.upsert_autonomous_run(autonomous_run=run)
        return {"autonomous_run": self._autonomous_run_public_summary(run, current_time=current_time)}

    def cancel_autonomous_run_api(self, token: str | None, run_id: str) -> dict[str, Any]:
        # 認可
        state = self._require_token(token)
        run = self._require_autonomous_run(run_id)
        self._require_autonomous_run_memory_set(
            run=run,
            memory_set_id=str(state["selected_memory_set_id"]),
        )
        current_time = self._now_iso()
        if run.get("status") not in AUTONOMOUS_RUN_TERMINAL_STATUSES:
            run = self._terminal_autonomous_run(
                run=run,
                current_time=current_time,
                status="cancelled",
                reason_summary="手動操作により autonomous_run を cancel した。",
            )
            self.store.upsert_autonomous_run(autonomous_run=run)
            run = self._finalize_autonomous_run_commitments(
                state=state,
                run=run,
                terminal_status="cancelled",
                current_time=current_time,
                evidence_events=[],
            )
        return {"autonomous_run": self._autonomous_run_public_summary(run, current_time=current_time)}

    def _require_autonomous_run(self, run_id: str) -> dict[str, Any]:
        if not isinstance(run_id, str) or not run_id.strip():
            raise ServiceError(400, "invalid_autonomous_run_id", "run_id must be a non-empty string.")
        run = self.store.get_autonomous_run(run_id=run_id.strip())
        if not isinstance(run, dict):
            raise ServiceError(404, "autonomous_run_not_found", "The requested autonomous_run does not exist.")
        return run

    def _require_autonomous_run_memory_set(self, *, run: dict[str, Any], memory_set_id: str) -> None:
        if run.get("memory_set_id") != memory_set_id:
            raise ServiceError(404, "autonomous_run_not_found", "The requested autonomous_run does not exist.")

    def _autonomous_run_execution_lock(self, run_id: str) -> threading.RLock:
        normalized_run_id = str(run_id or "").strip()
        with self._runtime_state_lock:
            lock = self._autonomous_run_execution_locks.get(normalized_run_id)
            if lock is None:
                lock = threading.RLock()
                self._autonomous_run_execution_locks[normalized_run_id] = lock
            return lock

    def _autonomous_run_due_for_step(self, *, run: dict[str, Any], current_time: str) -> bool:
        status = run.get("status")
        if status == "active":
            return True
        if status != "waiting_timer":
            return False
        next_run_at = run.get("next_run_at")
        if not isinstance(next_run_at, str) or not next_run_at.strip():
            return False
        try:
            return self._parse_iso(next_run_at.strip()) <= self._parse_iso(current_time)
        except ValueError:
            debug_log(
                "AutonomousRun",
                f"invalid next_run_at run={run.get('run_id')} next_run_at={self._clamp(next_run_at)}",
                level="ERROR",
            )
            return False

    def _autonomous_run_step_guard(
        self,
        *,
        run_id: str,
        current_time: str,
        allow_during_user_response: bool,
    ) -> dict[str, Any] | None:
        run = self.store.get_autonomous_run(run_id=run_id)
        if not isinstance(run, dict):
            return {"status": "missing"}
        status = run.get("status")
        if status in AUTONOMOUS_RUN_TERMINAL_STATUSES or status == "paused":
            return {"status": str(status or "inactive"), "autonomous_run": run}
        if status == "waiting_result":
            return {"status": "waiting_result", "autonomous_run": run}
        if not self._autonomous_run_due_for_step(run=run, current_time=current_time):
            return {"status": "not_due", "autonomous_run": run}
        if not allow_during_user_response and self._user_response_cycle_active():
            paused = self._pause_autonomous_run_for_user_interaction(
                run=run,
                current_time=current_time,
            )
            return {"status": "paused", "autonomous_run": paused or run}
        return None

    def _finish_autonomous_source_request_on_hold(
        self,
        *,
        source_request_record: dict[str, Any] | None,
        current_time: str,
        reason_summary: str,
    ) -> bool:
        if not isinstance(source_request_record, dict):
            return False
        self._finish_capability_ongoing_action(
            request_record=source_request_record,
            current_time=current_time,
            terminal_kind="on_hold",
            reason_code="autonomous_run_step_on_hold",
            terminal_reason=reason_summary,
            final_step_summary="autonomous_run step を保留した。",
            transition_source="autonomous_run_step",
            decision_kind="autonomous_step:on_hold",
            result_error=False,
            detail_summary=reason_summary,
        )
        return True

    def _autonomous_run_orphaned_request_record(self, run: dict[str, Any]) -> dict[str, Any]:
        last_step = run.get("last_step")
        action = last_step.get("action") if isinstance(last_step, dict) else None
        capability_request = action.get("capability_request") if isinstance(action, dict) else None
        input_payload = capability_request.get("input") if isinstance(capability_request, dict) else None
        capability_id = capability_request.get("capability_id") if isinstance(capability_request, dict) else None
        request_record: dict[str, Any] = {
            "request_id": run.get("waiting_request_id"),
            "autonomous_run_id": run.get("run_id"),
            "memory_set_id": run.get("memory_set_id"),
            "capability_id": capability_id if isinstance(capability_id, str) else "unknown",
            "input": input_payload if isinstance(input_payload, dict) else {},
        }
        if isinstance(input_payload, dict):
            for key in ("vision_source_id", "operation", "amount", "mcp_server_id", "tool_name"):
                value = input_payload.get(key)
                if isinstance(value, str) and value.strip():
                    request_record[key] = value.strip()
        for key in ("vision_source_id", "source_kind", "source_owner", "source_label"):
            value = run.get(key)
            if isinstance(value, str) and value.strip():
                request_record[key] = value.strip()
        return request_record

    def _autonomous_run_interrupted_result_context(
        self,
        *,
        request_record: dict[str, Any],
        reason_code: str,
        reason_summary: str,
    ) -> dict[str, Any]:
        capability_id = request_record.get("capability_id")
        source_capability_id = (
            capability_id.strip()
            if isinstance(capability_id, str) and capability_id.strip()
            else "unknown"
        )
        source_request_summary: dict[str, Any] = {
            "request_id": request_record.get("request_id"),
            "capability_id": source_capability_id,
            "status": reason_code,
        }
        timeout_ms = request_record.get("timeout_ms")
        if isinstance(timeout_ms, int):
            source_request_summary["timeout_ms"] = timeout_ms
        for key in (
            "vision_source_id",
            "source_kind",
            "source_owner",
            "source_label",
            "operation",
            "amount",
            "mcp_server_id",
            "tool_name",
        ):
            value = request_record.get(key)
            if isinstance(value, str) and value.strip():
                source_request_summary[key] = value.strip()
        return {
            "source_capability_id": source_capability_id,
            "allowed_followup_capability_ids": [source_capability_id],
            "followup_policy_summary": (
                "capability result 待ちが成立しなかったため、"
                "目的に照らして再試行、待機、完了、cancel を判断する。"
            ),
            "source_request_summary": source_request_summary,
            "observation_summary": {
                "result_status": "failed",
                "error_kind": reason_code,
                "summary_text": reason_summary,
            },
        }

    def _append_autonomous_interrupted_result_history(
        self,
        *,
        run: dict[str, Any],
        reason_summary: str,
    ) -> str:
        existing = str(run.get("history_summary") or "").strip()
        entry = f"result={reason_summary}"
        return f"{existing} / {entry}" if existing else entry

    def _mark_autonomous_run_capability_wait_interrupted(
        self,
        *,
        request_record: dict[str, Any],
        current_time: str,
        reason_code: str,
        reason_summary: str,
    ) -> dict[str, Any] | None:
        run_id = request_record.get("autonomous_run_id")
        request_id = request_record.get("request_id")
        if not isinstance(run_id, str) or not run_id.strip():
            return None
        if not isinstance(request_id, str) or not request_id.strip():
            return None

        with self._autonomous_run_execution_lock(run_id.strip()):
            run = self.store.get_autonomous_run(run_id=run_id.strip())
            if not isinstance(run, dict):
                return None
            if run.get("status") in AUTONOMOUS_RUN_TERMINAL_STATUSES:
                return None
            if run.get("status") not in {"waiting_result", "paused"}:
                return None
            if run.get("waiting_request_id") != request_id.strip():
                return None

            last_result_context = self._autonomous_run_interrupted_result_context(
                request_record=request_record,
                reason_code=reason_code,
                reason_summary=reason_summary,
            )
            updated = {
                **run,
                "waiting_request_id": None,
                "last_result_context": last_result_context,
                "history_summary": self._append_autonomous_interrupted_result_history(
                    run=run,
                    reason_summary=reason_summary,
                ),
                "updated_at": current_time,
            }
            if run.get("status") == "paused":
                updated["resume_status"] = "active"
            else:
                updated.update(
                    {
                        "status": "active",
                        "next_run_at": current_time,
                        "pause_reason": None,
                        "resume_status": None,
                    }
                )
            self.store.upsert_autonomous_run(autonomous_run=updated)
            debug_log(
                "AutonomousRun",
                (
                    f"capability wait interrupted run={run_id.strip()} request={request_id.strip()} "
                    f"reason={reason_code}"
                ),
                level="WARNING",
            )
            return updated

    def _background_autonomous_run_loop(self, stop_event: threading.Event) -> None:
        while not stop_event.wait(AUTONOMOUS_RUN_POLL_SECONDS):
            try:
                state = self.store.read_state()
                current_time = self._now_iso()
                self._prune_pending_capability_requests(current_time=current_time)
                due_runs = self.store.list_due_autonomous_runs(
                    memory_set_id=state["selected_memory_set_id"],
                    current_time=current_time,
                    limit=3,
                )
                for run in due_runs:
                    if stop_event.is_set():
                        return
                    if not self._cycle_coordinator.try_enter_background():
                        continue
                    try:
                        with self._wake_execution_lock:
                            self._execute_autonomous_run_step(
                                state=state,
                                run_id=str(run.get("run_id") or ""),
                                started_at=self._now_iso(),
                                emit_speech_event=True,
                            )
                    finally:
                        self._cycle_coordinator.leave_background()
            except Exception as exc:  # noqa: BLE001
                debug_log(
                    "AutonomousRun",
                    f"scheduler iteration failed error={type(exc).__name__}: {self._clamp(str(exc))}",
                    level="ERROR",
                )

    def _decision_autonomous_run_coordination(
        self,
        *,
        state: dict[str, Any],
        run_payload: dict[str, Any],
    ) -> dict[str, Any]:
        coordination = run_payload.get("coordination")
        if not isinstance(coordination, dict):
            raise ValueError("Decision autonomous_run.coordination is invalid.")
        mode = str(coordination.get("mode") or "").strip()
        target_run_ids = coordination.get("target_run_ids")
        reason_summary = str(coordination.get("reason_summary") or "").strip()
        if mode not in {"create_new", "replace_existing"}:
            raise ValueError("Decision autonomous_run.coordination.mode is invalid.")
        if not isinstance(target_run_ids, list) or not all(
            isinstance(run_id, str) and run_id.strip()
            for run_id in target_run_ids
        ):
            raise ValueError("Decision autonomous_run.coordination.target_run_ids is invalid.")
        normalized_target_run_ids = [str(run_id).strip() for run_id in target_run_ids]
        if len(set(normalized_target_run_ids)) != len(normalized_target_run_ids):
            raise ValueError("Decision autonomous_run.coordination.target_run_ids contains duplicates.")
        if mode == "create_new" and normalized_target_run_ids:
            raise ValueError("Decision autonomous_run.coordination create_new must not target existing runs.")
        if mode == "replace_existing" and not normalized_target_run_ids:
            raise ValueError("Decision autonomous_run.coordination replace_existing requires target runs.")
        if not reason_summary:
            raise ValueError("Decision autonomous_run.coordination.reason_summary is invalid.")
        target_runs = self._autonomous_run_coordination_target_runs(
            memory_set_id=str(state["selected_memory_set_id"]),
            target_run_ids=normalized_target_run_ids,
        )
        return {
            "mode": mode,
            "target_run_ids": normalized_target_run_ids,
            "reason_summary": reason_summary,
            "target_runs": target_runs,
        }

    def _autonomous_run_coordination_target_runs(
        self,
        *,
        memory_set_id: str,
        target_run_ids: list[str],
    ) -> list[dict[str, Any]]:
        runs: list[dict[str, Any]] = []
        for run_id in target_run_ids:
            run = self.store.get_autonomous_run(run_id=run_id)
            if not isinstance(run, dict):
                raise ValueError(f"Decision autonomous_run.coordination target run is missing: {run_id}")
            if run.get("memory_set_id") != memory_set_id:
                raise ValueError(f"Decision autonomous_run.coordination target run is outside memory_set: {run_id}")
            if run.get("status") in AUTONOMOUS_RUN_TERMINAL_STATUSES:
                raise ValueError(f"Decision autonomous_run.coordination target run is terminal: {run_id}")
            runs.append(run)
        return runs

    def _append_autonomous_run_coordination_history(
        self,
        *,
        run: dict[str, Any],
        mode: str,
        reason_summary: str,
    ) -> str:
        existing = str(run.get("history_summary") or "").strip()
        entry = f"coordination={mode} reason={reason_summary}"
        merged = f"{existing} / {entry}" if existing else entry
        return merged

    def _start_autonomous_run_from_decision(
        self,
        *,
        state: dict[str, Any],
        current_time: str,
        decision: dict[str, Any],
        source_current_input: dict[str, Any],
        source_cycle_id: str | None,
        assistant_message_target_client_id: str | None,
    ) -> dict[str, Any]:
        run_payload = decision.get("autonomous_run")
        if not isinstance(run_payload, dict):
            raise ValueError("Decision autonomous_run is invalid.")
        coordination = self._decision_autonomous_run_coordination(
            state=state,
            run_payload=run_payload,
        )
        objective_summary = str(run_payload.get("objective_summary") or "").strip()
        if not objective_summary:
            raise ValueError("Decision autonomous_run.objective_summary is invalid.")
        current_step_summary = str(run_payload.get("initial_step_summary") or "").strip()
        if not current_step_summary:
            current_step_summary = "autonomous_run の最初の一手を判断する。"
        origin_kind = str(source_current_input.get("source_kind") or "user_message").strip() or "user_message"
        run = {
            "run_id": f"autonomous_run:{uuid.uuid4().hex}",
            "memory_set_id": state["selected_memory_set_id"],
            "status": "active",
            "objective_summary": objective_summary,
            "origin_kind": origin_kind,
            "current_step_summary": current_step_summary,
            "history_summary": "",
            "observed_result_summaries": [],
            "observed_persons": [],
            "result_events": [],
            "next_run_at": current_time,
            "waiting_request_id": None,
            "pause_reason": None,
            "created_at": current_time,
            "updated_at": current_time,
            "completed_at": None,
            "source_current_input": deepcopy(source_current_input),
            "coordination": {
                "mode": coordination["mode"],
                "target_run_ids": list(coordination["target_run_ids"]),
                "reason_summary": coordination["reason_summary"],
            },
        }
        if coordination["mode"] == "replace_existing":
            self._replace_existing_autonomous_runs_from_decision(
                state=state,
                target_runs=coordination["target_runs"],
                current_time=current_time,
                reason_summary=str(coordination["reason_summary"]),
            )
        requested_by_person_ref = source_current_input.get("sender_ref")
        if (
            isinstance(requested_by_person_ref, str)
            and requested_by_person_ref.startswith("person:")
        ):
            run["requested_by_person_ref"] = requested_by_person_ref
        interaction_context = source_current_input.get("interaction_context")
        if isinstance(interaction_context, dict):
            origin_interaction_ref = interaction_context.get("interaction_ref")
            if isinstance(origin_interaction_ref, str) and origin_interaction_ref:
                run["origin_interaction_ref"] = origin_interaction_ref
            participants = interaction_context.get("participants")
            if isinstance(participants, list):
                run["participant_refs"] = [
                    participant["person_ref"]
                    for participant in participants
                    if (
                        isinstance(participant, dict)
                        and isinstance(participant.get("person_ref"), str)
                        and participant["person_ref"].startswith("person:")
                    )
                ]
        if isinstance(source_cycle_id, str) and source_cycle_id.strip():
            run["source_cycle_id"] = source_cycle_id.strip()
        normalized_target = self._normalize_capability_client_id(assistant_message_target_client_id)
        if normalized_target is not None:
            run["assistant_message_target_client_id"] = normalized_target
        self.store.upsert_autonomous_run(autonomous_run=run)
        debug_log(
            "AutonomousRun",
            (
                f"started run={run['run_id']} origin={origin_kind} mode={coordination['mode']} "
                f"targets={self._format_id_list_for_log(list(coordination['target_run_ids']))} "
                f"objective={self._clamp(objective_summary)}"
            ),
        )
        step_result = self._execute_autonomous_run_step(
            state=state,
            run_id=run["run_id"],
            started_at=current_time,
            source_current_input=source_current_input,
            emit_speech_event=False,
            allow_during_user_response=True,
        )
        return {
            "autonomous_run": self.store.get_autonomous_run(run_id=run["run_id"]) or run,
            "step_result": step_result,
        }

    def _replace_existing_autonomous_runs_from_decision(
        self,
        *,
        state: dict[str, Any],
        target_runs: list[dict[str, Any]],
        current_time: str,
        reason_summary: str,
    ) -> list[str]:
        replaced: list[str] = []
        for run in target_runs:
            run_id = str(run.get("run_id") or "").strip()
            with self._autonomous_run_execution_lock(run_id):
                current = self.store.get_autonomous_run(run_id=run_id) or run
                if current.get("status") in AUTONOMOUS_RUN_TERMINAL_STATUSES:
                    continue
                updated = self._terminal_autonomous_run(
                    run=current,
                    current_time=current_time,
                    status="cancelled",
                    reason_summary=reason_summary,
                )
                updated["history_summary"] = self._append_autonomous_run_coordination_history(
                    run=current,
                    mode="replace_existing",
                    reason_summary=reason_summary,
                )
                self.store.upsert_autonomous_run(autonomous_run=updated)
                updated = self._finalize_autonomous_run_commitments(
                    state=state,
                    run=updated,
                    terminal_status="cancelled",
                    current_time=current_time,
                    evidence_events=[],
                )
                replaced.append(run_id)
        if replaced:
            debug_log("AutonomousRun", f"replaced runs={','.join(replaced)} reason={self._clamp(reason_summary)}")
        return replaced

    def _execute_autonomous_run_step(
        self,
        *,
        state: dict[str, Any],
        run_id: str,
        started_at: str,
        source_current_input: dict[str, Any] | None = None,
        last_result_context: dict[str, Any] | None = None,
        source_request_record: dict[str, Any] | None = None,
        emit_speech_event: bool,
        allow_during_user_response: bool = False,
    ) -> dict[str, Any]:
        with self._autonomous_run_execution_lock(run_id):
            return self._execute_autonomous_run_step_locked(
                state=state,
                run_id=run_id,
                started_at=started_at,
                source_current_input=source_current_input,
                last_result_context=last_result_context,
                source_request_record=source_request_record,
                emit_speech_event=emit_speech_event,
                allow_during_user_response=allow_during_user_response,
            )

    def _execute_autonomous_run_step_locked(
        self,
        *,
        state: dict[str, Any],
        run_id: str,
        started_at: str,
        source_current_input: dict[str, Any] | None = None,
        last_result_context: dict[str, Any] | None = None,
        source_request_record: dict[str, Any] | None = None,
        emit_speech_event: bool,
        allow_during_user_response: bool,
    ) -> dict[str, Any]:
        guard_result = self._autonomous_run_step_guard(
            run_id=run_id,
            current_time=started_at,
            allow_during_user_response=allow_during_user_response,
        )
        if guard_result is not None:
            previous_request_finished = self._finish_autonomous_source_request_on_hold(
                source_request_record=source_request_record,
                current_time=self._now_iso(),
                reason_summary="run 状態またはユーザー応答中のため autonomous_run step を開始しない。",
            )
            return {
                **guard_result,
                "speech_payload": None,
                "capability_request_summary": None,
                "previous_request_finished": previous_request_finished,
                "step": None,
            }
        run = self.store.get_autonomous_run(run_id=run_id)
        if not isinstance(run, dict):
            return {"status": "missing"}
        if run.get("status") in AUTONOMOUS_RUN_TERMINAL_STATUSES or run.get("status") == "paused":
            return {"status": str(run.get("status") or "inactive")}
        if run.get("status") == "waiting_result":
            return {"status": "waiting_result"}

        speech_payload: dict[str, Any] | None = None
        capability_request_summary: dict[str, Any] | None = None
        speech_events: list[dict[str, Any]] = []
        previous_request_finished = False

        try:
            current_time = started_at
            selected_preset = state["model_presets"][state["selected_model_preset_id"]]
            step_context = self._build_autonomous_step_context(
                state=state,
                run=run,
                current_time=current_time,
                source_current_input=source_current_input,
                last_result_context=last_result_context or run.get("last_result_context"),
            )
            step = self.llm.generate_autonomous_step(
                model_config=selected_preset,
                persona_context=self._build_selected_persona_context(
                    state=state,
                    role="autonomous_step_generation",
                ),
                context=step_context,
            )
            action = step["action"]
            transition = step["transition"]
            action_kind = str(action.get("kind") or "").strip()

            current_time = self._now_iso()
            guard_result = self._autonomous_run_step_guard(
                run_id=run_id,
                current_time=current_time,
                allow_during_user_response=allow_during_user_response,
            )
            if guard_result is not None:
                previous_request_finished = self._finish_autonomous_source_request_on_hold(
                    source_request_record=source_request_record,
                    current_time=current_time,
                    reason_summary="ユーザー応答中または run 状態変更により autonomous_run step を保留した。",
                )
                return {
                    **guard_result,
                    "speech_payload": None,
                    "capability_request_summary": None,
                    "previous_request_finished": previous_request_finished,
                    "step": step,
                }
            run = self.store.get_autonomous_run(run_id=run_id) or run

            if action_kind == "speech":
                speech_payload = self._generate_autonomous_run_speech(
                    state=state,
                    selected_preset=selected_preset,
                    step_context=step_context,
                    step=step,
                )
                current_time = self._now_iso()
                guard_result = self._autonomous_run_step_guard(
                    run_id=run_id,
                    current_time=current_time,
                    allow_during_user_response=allow_during_user_response,
                )
                if guard_result is not None:
                    previous_request_finished = self._finish_autonomous_source_request_on_hold(
                        source_request_record=source_request_record,
                        current_time=current_time,
                        reason_summary="ユーザー応答中または run 状態変更により autonomous_run speech を保留した。",
                    )
                    return {
                        **guard_result,
                        "speech_payload": None,
                        "capability_request_summary": None,
                        "previous_request_finished": previous_request_finished,
                        "step": step,
                    }
                run = self.store.get_autonomous_run(run_id=run_id) or run
                if emit_speech_event:
                    self._emit_autonomous_run_assistant_message_event(
                        state=state,
                        run=run,
                        speech_payload=speech_payload,
                    )
                    speech_event = self._persist_autonomous_run_speech_event(
                        run=run,
                        speech_payload=speech_payload,
                        created_at=current_time,
                        step=step,
                        transition=transition,
                    )
                    if isinstance(speech_event, dict):
                        speech_events.append(speech_event)
            elif action_kind == "capability_request":
                current_time = self._now_iso()
                guard_result = self._autonomous_run_step_guard(
                    run_id=run_id,
                    current_time=current_time,
                    allow_during_user_response=allow_during_user_response,
                )
                if guard_result is not None:
                    previous_request_finished = self._finish_autonomous_source_request_on_hold(
                        source_request_record=source_request_record,
                        current_time=current_time,
                        reason_summary="ユーザー応答中または run 状態変更により autonomous_run capability request を保留した。",
                    )
                    return {
                        **guard_result,
                        "speech_payload": None,
                        "capability_request_summary": None,
                        "previous_request_finished": previous_request_finished,
                        "step": step,
                    }
                run = self.store.get_autonomous_run(run_id=run_id) or run
                try:
                    step_source_current_input = step_context.current_input.to_prompt_payload()
                    step_activation = self._agent_skill_activation_summary(
                        getattr(step_context, "agent_skill_context", None)
                    )
                    if step_activation is not None:
                        step_source_current_input["agent_skill_activation"] = step_activation
                    capability_request_summary = self._dispatch_autonomous_run_capability_request(
                        state=state,
                        run=run,
                        current_time=current_time,
                        action=action,
                        source_current_input=step_source_current_input,
                    )
                except PreSendCheckWithheldError as first_withhold:
                    # autonomous step も候補本文を戻さず、同じ run 文脈で一度だけ再生成する。
                    step_context = self._build_autonomous_step_context(
                        state=state,
                        run=run,
                        current_time=self._now_iso(),
                        source_current_input=source_current_input,
                        last_result_context=last_result_context or run.get("last_result_context"),
                        pre_send_check_feedback=AUTONOMOUS_PRE_SEND_CHECK_RETRY_FEEDBACK,
                    )
                    step = self.llm.generate_autonomous_step(
                        model_config=selected_preset,
                        persona_context=self._build_selected_persona_context(
                            state=state,
                            role="autonomous_step_generation",
                        ),
                        context=step_context,
                    )
                    action = step["action"]
                    transition = step["transition"]
                    action_kind = str(action.get("kind") or "").strip()
                    current_time = self._now_iso()
                    guard_result = self._autonomous_run_step_guard(
                        run_id=run_id,
                        current_time=current_time,
                        allow_during_user_response=allow_during_user_response,
                    )
                    if guard_result is not None:
                        previous_request_finished = self._finish_autonomous_source_request_on_hold(
                            source_request_record=source_request_record,
                            current_time=current_time,
                            reason_summary="状態変更により pre-send check 後の autonomous_run step を保留した。",
                        )
                        return {
                            **guard_result,
                            "speech_payload": None,
                            "capability_request_summary": None,
                            "previous_request_finished": previous_request_finished,
                            "step": step,
                        }
                    run = self.store.get_autonomous_run(run_id=run_id) or run
                    if action_kind == "capability_request":
                        step_source_current_input = step_context.current_input.to_prompt_payload()
                        step_activation = self._agent_skill_activation_summary(
                            getattr(step_context, "agent_skill_context", None)
                        )
                        if step_activation is not None:
                            step_source_current_input["agent_skill_activation"] = step_activation
                        capability_request_summary = self._dispatch_autonomous_run_capability_request(
                            state=state,
                            run=run,
                            current_time=current_time,
                            action=action,
                            source_current_input=step_source_current_input,
                            pre_send_check_attempt=2,
                            pre_send_check_prior_attempts=[deepcopy(first_withhold.audit_summary)],
                        )
                        if "pre_send_check" not in capability_request_summary:
                            self._persist_autonomous_pre_send_check_audit(
                                run=run,
                                current_time=current_time,
                                audit_summary={
                                    "result_status": "recovered",
                                    "attempts": [deepcopy(first_withhold.audit_summary)],
                                },
                            )
                    elif action_kind == "speech":
                        speech_payload = self._generate_autonomous_run_speech(
                            state=state,
                            selected_preset=selected_preset,
                            step_context=step_context,
                            step=step,
                        )
                        current_time = self._now_iso()
                        guard_result = self._autonomous_run_step_guard(
                            run_id=run_id,
                            current_time=current_time,
                            allow_during_user_response=allow_during_user_response,
                        )
                        if guard_result is not None:
                            previous_request_finished = self._finish_autonomous_source_request_on_hold(
                                source_request_record=source_request_record,
                                current_time=current_time,
                                reason_summary="状態変更により pre-send check 後の autonomous_run speech を保留した。",
                            )
                            return {
                                **guard_result,
                                "speech_payload": None,
                                "capability_request_summary": None,
                                "previous_request_finished": previous_request_finished,
                                "step": step,
                            }
                        run = self.store.get_autonomous_run(run_id=run_id) or run
                        if emit_speech_event:
                            self._emit_autonomous_run_assistant_message_event(
                                state=state,
                                run=run,
                                speech_payload=speech_payload,
                            )
                            speech_event = self._persist_autonomous_run_speech_event(
                                run=run,
                                speech_payload=speech_payload,
                                created_at=current_time,
                                step=step,
                                transition=transition,
                            )
                            if isinstance(speech_event, dict):
                                speech_events.append(speech_event)
                        self._persist_autonomous_pre_send_check_audit(
                            run=run,
                            current_time=current_time,
                            audit_summary={
                                "result_status": "recovered",
                                "attempts": [deepcopy(first_withhold.audit_summary)],
                            },
                        )
                    elif action_kind == "none":
                        self._record_autonomous_pre_send_check_terminal(
                            run=run,
                            current_time=current_time,
                            reason_code="pre_send_check_retry_noop",
                            audit_summary={
                                "result_status": "withheld",
                                "attempts": [deepcopy(first_withhold.audit_summary)],
                            },
                            message=AUTONOMOUS_PRE_SEND_CHECK_WITHHELD_NOTICE,
                        )

            current_time = self._now_iso()
            if action_kind == "none" or (action_kind == "speech" and not emit_speech_event):
                guard_result = self._autonomous_run_step_guard(
                    run_id=run_id,
                    current_time=current_time,
                    allow_during_user_response=allow_during_user_response,
                )
                if guard_result is not None:
                    previous_request_finished = self._finish_autonomous_source_request_on_hold(
                        source_request_record=source_request_record,
                        current_time=current_time,
                        reason_summary="ユーザー応答中または run 状態変更により autonomous_run transition を保留した。",
                    )
                    return {
                        **guard_result,
                        "speech_payload": None,
                        "capability_request_summary": None,
                        "previous_request_finished": previous_request_finished,
                        "step": step,
                    }
            run = self.store.get_autonomous_run(run_id=run_id) or run

            if action_kind != "capability_request" and isinstance(source_request_record, dict):
                previous_request_finished = True
                step_reason_summary = self._autonomous_step_reason_summary(
                    step,
                    fallback="autonomous_run step で継続を更新した。",
                )
                self._finish_capability_ongoing_action(
                    request_record=source_request_record,
                    current_time=current_time,
                    terminal_kind=self._autonomous_run_ongoing_action_terminal_kind(transition),
                    reason_code=f"autonomous_run:{transition.get('kind')}",
                    terminal_reason=step_reason_summary,
                    final_step_summary=str(step.get("run_update", {}).get("current_step_summary") or "autonomous_run step を処理した。"),
                    transition_source="autonomous_run_step",
                    decision_kind=f"autonomous_step:{action_kind}",
                    result_error=transition.get("kind") == "cancel",
                    detail_summary=step_reason_summary,
                )

            updated_run = self._apply_autonomous_step_transition(
                run=run,
                step=step,
                action_kind=action_kind,
                current_time=current_time,
                capability_request_summary=capability_request_summary,
            )
            if updated_run.get("status") in AUTONOMOUS_RUN_TERMINAL_STATUSES:
                updated_run = self._finalize_autonomous_run_commitments(
                    state=state,
                    run=updated_run,
                    terminal_status=str(updated_run.get("status") or ""),
                    current_time=current_time,
                    evidence_events=speech_events,
                )
        except (PreSendCheckWithheldError, PreSendCheckFailureError) as exc:
            current_time = self._now_iso()
            run = self.store.get_autonomous_run(run_id=run_id) or run
            is_failure = isinstance(exc, PreSendCheckFailureError)
            reason_code = (
                "pre_send_check_failure"
                if is_failure
                else "pre_send_check_withheld"
            )
            updated_run = self._terminal_autonomous_run(
                run=run,
                current_time=current_time,
                status="cancelled",
                reason_summary="送信前チェックの結果、外部送信を行わず autonomous_run を終了した。",
            )
            self.store.upsert_autonomous_run(autonomous_run=updated_run)
            updated_run = self._finalize_autonomous_run_commitments(
                state=state,
                run=updated_run,
                terminal_status="cancelled",
                current_time=current_time,
                evidence_events=[],
            )
            self._record_autonomous_pre_send_check_terminal(
                run=updated_run,
                current_time=current_time,
                reason_code=reason_code,
                audit_summary=exc.audit_summary,
                message=(
                    AUTONOMOUS_PRE_SEND_CHECK_FAILURE_NOTICE
                    if is_failure
                    else AUTONOMOUS_PRE_SEND_CHECK_WITHHELD_NOTICE
                ),
            )
            if isinstance(source_request_record, dict):
                previous_request_finished = True
                self._finish_capability_ongoing_action(
                    request_record=source_request_record,
                    current_time=current_time,
                    terminal_kind="interrupted",
                    reason_code=reason_code,
                    terminal_reason="送信前チェックのため autonomous_run を終了した。",
                    final_step_summary="外部送信を行わず終了した。",
                    transition_source="autonomous_run_step",
                    decision_kind="autonomous_step:none",
                    result_error=is_failure,
                    detail_summary=reason_code,
                )
            debug_log(
                "AutonomousRun",
                f"step stopped run={run_id} reason={reason_code}",
                level="WARNING" if not is_failure else "ERROR",
            )
            return {
                "status": updated_run.get("status"),
                "autonomous_run": updated_run,
                "speech_payload": None,
                "capability_request_summary": None,
                "previous_request_finished": previous_request_finished,
                "step": None,
                "error": reason_code,
                "pre_send_check": deepcopy(exc.audit_summary),
            }
        except (LLMError, KeyError, ValueError, CapabilityDispatchError) as exc:
            current_time = self._now_iso()
            run = self.store.get_autonomous_run(run_id=run_id) or run
            updated_run = self._terminal_autonomous_run(
                run=run,
                current_time=current_time,
                status="cancelled",
                reason_summary=f"autonomous_run step に失敗した: {str(exc).strip()}",
            )
            self.store.upsert_autonomous_run(autonomous_run=updated_run)
            updated_run = self._finalize_autonomous_run_commitments(
                state=state,
                run=updated_run,
                terminal_status="cancelled",
                current_time=current_time,
                evidence_events=[],
            )
            if isinstance(source_request_record, dict):
                previous_request_finished = True
                self._finish_capability_ongoing_action(
                    request_record=source_request_record,
                    current_time=current_time,
                    terminal_kind="interrupted",
                    reason_code="autonomous_run_step_failed",
                    terminal_reason=str(updated_run.get("pause_reason") or "autonomous_run step に失敗した。"),
                    final_step_summary="autonomous_run step に失敗したため終了した。",
                    transition_source="autonomous_run_step",
                    decision_kind="autonomous_step:failed",
                    result_error=True,
                    detail_summary=str(exc),
                )
            debug_log(
                "AutonomousRun",
                f"step failed run={run_id} error={type(exc).__name__}: {self._clamp(str(exc))}",
                level="ERROR",
            )
            return {
                "status": updated_run.get("status"),
                "autonomous_run": updated_run,
                "speech_payload": None,
                "capability_request_summary": None,
                "previous_request_finished": previous_request_finished,
                "step": None,
                "error": str(exc),
            }
        debug_log(
            "AutonomousRun",
            (
                f"step run={run_id} action={action_kind} transition={transition.get('kind')} "
                f"status={updated_run.get('status')}"
            ),
        )
        return {
            "status": updated_run.get("status"),
            "autonomous_run": updated_run,
            "speech_payload": speech_payload,
            "capability_request_summary": capability_request_summary,
            "previous_request_finished": previous_request_finished,
            "step": step,
        }

    def _build_autonomous_step_context(
        self,
        *,
        state: dict[str, Any],
        run: dict[str, Any],
        current_time: str,
        source_current_input: dict[str, Any] | None,
        last_result_context: dict[str, Any] | None,
        pre_send_check_feedback: str | None = None,
    ) -> AutonomousStepContext:
        current_input_payload = source_current_input if isinstance(source_current_input, dict) else None
        if current_input_payload is None:
            current_input_payload = {
                "sender_kind": "system",
                "sender_ref": None,
                "source_kind": "autonomous_run",
                "response_target_refs": [],
                "text": f"autonomous_run step: {run.get('objective_summary')}",
            }
        raw_interaction_context = current_input_payload.get("interaction_context")
        interaction_context = normalize_interaction_context(
            raw_interaction_context,
            required=False,
            require_speaker=False,
        )
        raw_response_target_refs = current_input_payload.get("response_target_refs")
        response_target_refs = (
            tuple(
                value.strip()
                for value in raw_response_target_refs
                if isinstance(value, str) and value.strip()
            )
            if isinstance(raw_response_target_refs, list)
            else ()
        )
        current_input = CurrentInput(
            sender_kind=str(current_input_payload.get("sender_kind") or "system"),
            sender_ref=(
                str(current_input_payload["sender_ref"]).strip()
                if isinstance(current_input_payload.get("sender_ref"), str)
                and str(current_input_payload["sender_ref"]).strip()
                else None
            ),
            source_kind=str(current_input_payload.get("source_kind") or "autonomous_run"),
            response_target_refs=response_target_refs,
            interaction_context=interaction_context,
            text=str(current_input_payload.get("text") or ""),
        )
        foreground_world_state = self._summarize_foreground_world_states(
            self._list_current_world_states(
                state=state,
                current_time=current_time,
                limit=6,
            ),
            current_time=current_time,
        )
        capability_decision_view = self._build_capability_decision_view(
            state=state,
            current_time=current_time,
        )
        recent_turns = self._load_recent_turns(state, interaction_context)
        agent_skill_context = self._build_agent_skill_context(
            model_config=state["model_presets"][state["selected_model_preset_id"]],
            current_input=current_input,
            trigger_kind="autonomous_run",
            capability_decision_view=capability_decision_view,
            recent_turns=recent_turns,
            run=self._autonomous_run_prompt_summary(run),
            prior_activation=(
                source_current_input.get("agent_skill_activation")
                if isinstance(source_current_input, dict)
                and isinstance(source_current_input.get("agent_skill_activation"), dict)
                else None
            ),
        )
        return AutonomousStepContext(
            run=self._autonomous_run_prompt_summary(run),
            current_input=current_input,
            recent_turns=recent_turns,
            time_context=self._build_time_context(current_time=current_time),
            foreground_world_state=foreground_world_state,
            activity_context=self._autonomous_run_activity_context(
                state=state,
                current_time=current_time,
                interaction_context=interaction_context,
            ),
            ongoing_action_summary=self._summarize_ongoing_action(
                self._current_ongoing_action(state=state, current_time=current_time)
            ),
            capability_decision_view=capability_decision_view,
            last_result_context=last_result_context if isinstance(last_result_context, dict) else None,
            people_context=self._build_people_context(
                state=state,
                current_input=current_input,
                structured_sources=[run, last_result_context],
            ),
            pre_send_check_feedback=pre_send_check_feedback,
            agent_skill_context=agent_skill_context,
        )

    def _autonomous_run_activity_context(
        self,
        *,
        state: dict[str, Any],
        current_time: str,
        interaction_context: Any,
    ) -> dict[str, Any] | None:
        # run起点の人物が確定している場合だけ、その人物の短期活動を読む。
        participant_refs = (
            interaction_context.participant_refs
            if interaction_context is not None
            else ()
        )
        if not participant_refs:
            return None
        activity_state = self.store.get_current_activity_state(
            memory_set_id=state["selected_memory_set_id"],
            actor_ref=participant_refs[0],
            current_time=current_time,
        )
        return self._summarize_activity_context(
            activity_state,
            current_time=current_time,
        )

    def _generate_autonomous_run_speech(
        self,
        *,
        state: dict[str, Any],
        selected_preset: dict[str, Any],
        step_context: AutonomousStepContext,
        step: dict[str, Any],
    ) -> dict[str, Any]:
        speech_action = step["action"].get("speech")
        if not isinstance(speech_action, dict):
            speech_action = {}
        reason_summary = self._autonomous_step_reason_summary(
            step,
            fallback="autonomous_run の一手として発話する。",
        )
        decision = {
            "kind": "speech",
            "reason_code": str(speech_action.get("reason_code") or "autonomous_run_speech").strip(),
            "reason_summary": reason_summary,
            "requires_confirmation": False,
            "pending_intent": None,
            "capability_request": None,
            "autonomous_run": None,
            "foreground_selection": {
                "primary_factor_ref": None,
                "supporting_factor_refs": [],
                "suppressed_factors": [],
                "summary_text": "autonomous_step_generation が発話一手を選んだ。",
            },
            "target_stances": build_decision_target_stances_for_kind(
                "speech",
                required_targets=("outward_speech",),
                reason_summary=reason_summary,
            ),
        }
        speech_context = self._build_speech_context(
            input_text=step_context.current_input.text,
            current_input=step_context.current_input,
            recent_turns=step_context.recent_turns,
            time_context=step_context.time_context,
            affect_context={
                "mood_state": {
                    "baseline_vad": {"v": 0.0, "a": 0.0, "d": 0.0},
                    "residual_vad": {"v": 0.0, "a": 0.0, "d": 0.0},
                    "current_vad": {"v": 0.0, "a": 0.0, "d": 0.0},
                    "confidence": 0.0,
                    "observed_at": None,
                    "created_at": None,
                    "updated_at": None,
                },
                "affect_states": [],
                "recent_episode_affects": [],
            },
            drive_state_summary=None,
            foreground_world_state=step_context.foreground_world_state,
            activity_context=step_context.activity_context,
            ongoing_action_summary=step_context.ongoing_action_summary,
            initiative_context=None,
            visual_observation_context=None,
            self_state_context=None,
            people_context=step_context.people_context or [],
            relationship_context=None,
            prediction_error_context=None,
            workspace_context=None,
            recall_hint=self._empty_recall_hint(),
            recall_pack=self._empty_recall_pack(),
            decision=decision,
            agent_skill_context=step_context.agent_skill_context,
        )
        return self.llm.generate_speech(
            model_config=selected_preset,
            persona_context=self._build_selected_persona_context(
                state=state,
                role="expression_generation",
                include_expression=True,
            ),
            context=speech_context,
        )

    def _dispatch_autonomous_run_capability_request(
        self,
        *,
        state: dict[str, Any],
        run: dict[str, Any],
        current_time: str,
        action: dict[str, Any],
        source_current_input: dict[str, Any],
        pre_send_check_attempt: int = 1,
        pre_send_check_prior_attempts: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        request_payload = action.get("capability_request")
        if not isinstance(request_payload, dict):
            raise ValueError("Autonomous step capability_request is invalid.")
        capability_id = request_payload.get("capability_id")
        input_payload = request_payload.get("input")
        if not isinstance(capability_id, str) or not capability_id.strip():
            raise ValueError("Autonomous step capability_id is invalid.")
        if not isinstance(input_payload, dict):
            raise ValueError("Autonomous step capability input must be an object.")
        run = self.store.get_autonomous_run(run_id=str(run.get("run_id") or "")) or run
        result = self._dispatch_capability_request(
            memory_set_id=state["selected_memory_set_id"],
            capability_id=capability_id.strip(),
            input_payload=input_payload,
            current_time=current_time,
            goal_summary=str(run.get("objective_summary") or capability_id).strip(),
            wait_for_response=False,
            component="AutonomousRun",
            source_current_input=source_current_input,
            assistant_message_target_client_id=self._request_run_assistant_message_target_client_id(run),
            track_ongoing_action=True,
            autonomous_run_id=str(run.get("run_id") or "").strip(),
            pre_send_check_attempt=pre_send_check_attempt,
            pre_send_check_prior_attempts=pre_send_check_prior_attempts,
        )
        if not isinstance(result, dict):
            raise ValueError("Autonomous capability dispatch failed.")
        summary = result.get("capability_request_summary")
        if not isinstance(summary, dict):
            raise ValueError("Autonomous capability dispatch summary is missing.")
        return summary

    def _apply_autonomous_step_transition(
        self,
        *,
        run: dict[str, Any],
        step: dict[str, Any],
        action_kind: str,
        current_time: str,
        capability_request_summary: dict[str, Any] | None,
    ) -> dict[str, Any]:
        transition = step["transition"]
        run_update = step["run_update"]
        transition_kind = str(transition.get("kind") or "").strip()
        updated = {
            **run,
            "current_step_summary": str(
                run_update.get("current_step_summary") or run.get("current_step_summary") or ""
            ).strip(),
            "history_summary": self._updated_autonomous_run_history(
                run=run,
                step=step,
                capability_request_summary=capability_request_summary,
            ),
            "updated_at": current_time,
            "last_step": deepcopy(step),
            "last_result_context": run.get("last_result_context"),
        }

        if action_kind == "capability_request":
            request_id = capability_request_summary.get("request_id") if isinstance(capability_request_summary, dict) else None
            updated.update(
                {
                    "status": "waiting_result",
                    "waiting_request_id": request_id,
                    "next_run_at": None,
                    "pause_reason": None,
                }
            )
        elif transition_kind == "wait_until":
            updated.update(
                {
                    "status": "waiting_timer",
                    "waiting_request_id": None,
                    "next_run_at": str(transition.get("next_run_at") or "").strip(),
                    "pause_reason": None,
                }
            )
        elif transition_kind == "complete":
            updated = self._terminal_autonomous_run(
                run=updated,
                current_time=current_time,
                status="completed",
                reason_summary=self._autonomous_step_reason_summary(
                    step,
                    fallback="autonomous_run が完了した。",
                ),
            )
        elif transition_kind == "cancel":
            updated = self._terminal_autonomous_run(
                run=updated,
                current_time=current_time,
                status="cancelled",
                reason_summary=self._autonomous_step_reason_summary(
                    step,
                    fallback="autonomous_run を cancel した。",
                ),
            )
        else:
            delay_seconds = AUTONOMOUS_RUN_CONTINUE_DELAY_SECONDS
            if action_kind == "none":
                delay_seconds = AUTONOMOUS_RUN_IDLE_CONTINUE_DELAY_SECONDS
            updated.update(
                {
                    "status": "waiting_timer",
                    "waiting_request_id": None,
                    "next_run_at": (self._parse_iso(current_time) + timedelta(seconds=delay_seconds)).isoformat(),
                    "pause_reason": None,
                }
            )
        self.store.upsert_autonomous_run(autonomous_run=updated)
        return updated

    def _updated_autonomous_run_history(
        self,
        *,
        run: dict[str, Any],
        step: dict[str, Any],
        capability_request_summary: dict[str, Any] | None,
    ) -> str:
        run_update = step.get("run_update")
        if isinstance(run_update, dict):
            history_summary = run_update.get("history_summary")
            if isinstance(history_summary, str) and history_summary.strip():
                return history_summary.strip()
        action_kind = step.get("action", {}).get("kind") if isinstance(step.get("action"), dict) else None
        transition_kind = step.get("transition", {}).get("kind") if isinstance(step.get("transition"), dict) else None
        step_summary = run_update.get("current_step_summary") if isinstance(run_update, dict) else None
        entry = f"action={action_kind} transition={transition_kind}"
        if isinstance(capability_request_summary, dict):
            entry += f" request={capability_request_summary.get('capability_id')}"
        if isinstance(step_summary, str) and step_summary.strip():
            entry += f" step={step_summary.strip()}"
        existing = str(run.get("history_summary") or "").strip()
        merged = f"{existing} / {entry}" if existing else entry
        return merged

    def _autonomous_step_reason_summary(self, step: dict[str, Any], *, fallback: str) -> str:
        # transition は状態だけを持つため、説明は action と run_update から作る。
        action = step.get("action")
        if isinstance(action, dict):
            speech = action.get("speech")
            if isinstance(speech, dict):
                reason_summary = speech.get("reason_summary")
                if isinstance(reason_summary, str) and reason_summary.strip():
                    return reason_summary.strip()
        run_update = step.get("run_update")
        if isinstance(run_update, dict):
            for key in ("current_step_summary", "history_summary"):
                value = run_update.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return fallback

    def _terminal_autonomous_run(
        self,
        *,
        run: dict[str, Any],
        current_time: str,
        status: str,
        reason_summary: str,
    ) -> dict[str, Any]:
        return {
            **run,
            "status": status,
            "waiting_request_id": None,
            "next_run_at": None,
            "pause_reason": reason_summary if status == "cancelled" else None,
            "completed_at": current_time,
            "updated_at": current_time,
        }

    def _autonomous_run_ongoing_action_terminal_kind(self, transition: dict[str, Any]) -> str:
        transition_kind = str(transition.get("kind") or "").strip()
        if transition_kind == "cancel":
            return "interrupted"
        if transition_kind == "wait_until":
            return "on_hold"
        return "completed"

    def _start_async_autonomous_capability_result_cycle(
        self,
        *,
        state: dict[str, Any],
        capability_response: dict[str, Any],
        started_at: str,
    ) -> None:
        request_record = capability_response.get("request_record")
        run_id = request_record.get("autonomous_run_id") if isinstance(request_record, dict) else None
        request_id = request_record.get("request_id") if isinstance(request_record, dict) else None
        request_label = request_id if isinstance(request_id, str) and request_id.strip() else "-"

        def run_cycle() -> None:
            try:
                self._execute_autonomous_capability_result_cycle(
                    state=state,
                    capability_response=capability_response,
                    started_at=started_at,
                )
            except Exception as exc:  # noqa: BLE001
                debug_log(
                    "AutonomousRun",
                    (
                        f"result cycle crashed request={request_label} run={run_id or '-'} "
                        f"error={type(exc).__name__}: {self._clamp(str(exc))}"
                    ),
                    level="ERROR",
                )

        thread = threading.Thread(
            target=run_cycle,
            name="otomekairo-autonomous-result",
            daemon=True,
        )
        thread.start()

    def _execute_autonomous_capability_result_cycle(
        self,
        *,
        state: dict[str, Any],
        capability_response: dict[str, Any],
        started_at: str,
    ) -> None:
        # autonomous_run の非同期結果も同じ個の状態更新として会話と直列化する。
        self._cycle_coordinator.enter_foreground()
        try:
            self._execute_autonomous_capability_result_cycle_inner(
                state=state,
                capability_response=capability_response,
                started_at=started_at,
            )
        finally:
            self._cycle_coordinator.leave_foreground()

    def _execute_autonomous_capability_result_cycle_inner(
        self,
        *,
        state: dict[str, Any],
        capability_response: dict[str, Any],
        started_at: str,
    ) -> None:
        request_record = capability_response.get("request_record")
        if not isinstance(request_record, dict):
            return
        run_id = request_record.get("autonomous_run_id")
        if not isinstance(run_id, str) or not run_id.strip():
            return
        run = self.store.get_autonomous_run(run_id=run_id.strip())
        if not isinstance(run, dict):
            return

        capability_id = self._capability_result_capability_id(capability_response)
        capability_request_summary = self._capability_request_summary(request_record)
        self._activate_capability_ongoing_action(
            request_record=request_record,
            current_time=started_at,
            active_step_summary=self._capability_result_active_step_summary(
                capability_id=capability_id,
                result_payload=capability_response,
            ),
        )
        client_context = self._build_capability_result_client_context(capability_response)
        observation_summary = self._capability_result_observation_summary(capability_response)
        input_text = self._build_capability_result_input_text(
            client_context=client_context,
            capability_response=capability_response,
        )
        try:
            client_context, observation_summary, input_text = self._prepare_capability_result_context(
                state=state,
                started_at=started_at,
                capability_id=capability_id,
                client_context=client_context,
                observation_summary=observation_summary,
                input_text=input_text,
                capability_response=capability_response,
            )
            last_result_context = self._build_capability_result_decision_context(
                trigger_kind="capability_result",
                observation_summary=observation_summary,
                capability_request_summary=capability_request_summary,
            )
            result_event = self._persist_autonomous_capability_result_event(
                run=run,
                capability_id=capability_id,
                observation_summary=observation_summary,
                input_text=input_text,
                created_at=started_at,
            )
            self._register_mcp_observed_persons(
                state=state,
                observation_summary=observation_summary,
                observed_at=started_at,
                evidence_event_ids=(
                    [result_event["event_id"]]
                    if isinstance(result_event, dict) and isinstance(result_event.get("event_id"), str)
                    else []
                ),
            )
            with self._autonomous_run_execution_lock(run_id.strip()):
                run = self.store.get_autonomous_run(run_id=run_id.strip()) or run
                if run.get("status") in AUTONOMOUS_RUN_TERMINAL_STATUSES:
                    self._finish_capability_ongoing_action(
                        request_record=request_record,
                        current_time=self._now_iso(),
                        terminal_kind="interrupted",
                        reason_code="autonomous_run_terminal_before_result",
                        terminal_reason="autonomous_run が terminal 状態のため result 後の step を進めない。",
                        final_step_summary="terminal run の result を受け取った。",
                        transition_source="autonomous_run_step",
                        result_error=False,
                    )
                    return
                next_status = "paused" if run.get("status") == "paused" else "active"
                updated_run = {
                    **run,
                    "status": next_status,
                    "waiting_request_id": None,
                    "next_run_at": started_at if next_status == "active" else run.get("next_run_at"),
                    "last_result_context": last_result_context,
                    "observed_result_summaries": self._append_autonomous_observed_result_summaries(
                        run=run,
                        capability_id=capability_id,
                        observation_summary=observation_summary,
                        result_payload=capability_response,
                        created_at=started_at,
                    ),
                    "observed_persons": self._merge_autonomous_observed_persons(
                        run=run,
                        observation_summary=observation_summary,
                    ),
                    "result_events": self._append_autonomous_result_events(
                        run=run,
                        result_event=result_event,
                    ),
                    "history_summary": self._append_autonomous_result_history(
                        run=run,
                        capability_id=capability_id,
                        observation_summary=observation_summary,
                        result_payload=capability_response,
                    ),
                    "updated_at": self._now_iso(),
                }
                self.store.upsert_autonomous_run(autonomous_run=updated_run)
                if updated_run.get("status") == "paused":
                    self._finish_autonomous_source_request_on_hold(
                        source_request_record=request_record,
                        current_time=self._now_iso(),
                        reason_summary="autonomous_run が pause 中のため capability result 後の step を保留した。",
                    )
                    return
                if self._user_response_cycle_active():
                    self._pause_autonomous_run_for_user_interaction(
                        run=updated_run,
                        current_time=self._now_iso(),
                    )
                    self._finish_autonomous_source_request_on_hold(
                        source_request_record=request_record,
                        current_time=self._now_iso(),
                        reason_summary="ユーザー応答中のため capability result 後の autonomous_run step を保留した。",
                    )
                    return
                self._execute_autonomous_run_step(
                    state=state,
                    run_id=run_id.strip(),
                    started_at=self._now_iso(),
                    last_result_context=last_result_context,
                    source_request_record=request_record,
                    emit_speech_event=True,
                    allow_during_user_response=False,
                )
        except (LLMError, KeyError, ValueError, CapabilityDispatchError) as exc:
            interrupted = self._terminal_autonomous_run(
                run=run,
                current_time=self._now_iso(),
                status="cancelled",
                reason_summary=f"capability result 後の autonomous_run step に失敗した: {str(exc).strip()}",
            )
            self.store.upsert_autonomous_run(autonomous_run=interrupted)
            interrupted = self._finalize_autonomous_run_commitments(
                state=state,
                run=interrupted,
                terminal_status="cancelled",
                current_time=self._now_iso(),
                evidence_events=[],
            )
            self._finish_capability_ongoing_action(
                request_record=request_record,
                current_time=self._now_iso(),
                terminal_kind="interrupted",
                reason_code="autonomous_run_step_failed",
                terminal_reason=str(interrupted.get("pause_reason") or "autonomous_run step に失敗した。"),
                final_step_summary="autonomous_run step に失敗したため終了した。",
                transition_source="autonomous_run_step",
                result_error=True,
                detail_summary=str(exc),
            )
            debug_log(
                "AutonomousRun",
                f"result cycle failed run={run_id} error={type(exc).__name__}: {self._clamp(str(exc))}",
                level="ERROR",
            )

    def _persist_autonomous_capability_result_event(
        self,
        *,
        run: dict[str, Any],
        capability_id: str,
        observation_summary: dict[str, Any] | None,
        input_text: str,
        created_at: str,
    ) -> dict[str, Any]:
        observed_persons = self._observed_persons_from_mcp_observation(observation_summary)
        event = {
            "event_id": f"event:{uuid.uuid4().hex}",
            "cycle_id": self._autonomous_run_event_cycle_id(run),
            "memory_set_id": run["memory_set_id"],
            "kind": "capability_result",
            "role": "system",
            "text": input_text,
            "created_at": created_at,
            "source_kind": "autonomous_run",
            "run_id": run.get("run_id"),
            "capability_id": capability_id,
            "mcp_server_id": (
                observation_summary.get("mcp_server_id")
                if isinstance(observation_summary, dict)
                else None
            ),
            "tool_name": (
                observation_summary.get("tool_name")
                if isinstance(observation_summary, dict)
                else None
            ),
            "mcp_result_summary": (
                observation_summary.get("mcp_result_summary")
                if isinstance(observation_summary, dict)
                else None
            ),
            "observed_persons": observed_persons,
            "observed_person_refs": [person["person_ref"] for person in observed_persons],
            "interaction_ref": run.get("origin_interaction_ref"),
            "speaker_ref": None,
            "participant_refs": run.get("participant_refs") or [],
        }
        self.store.append_events(events=[event])
        return event

    def _append_autonomous_observed_result_summaries(
        self,
        *,
        run: dict[str, Any],
        capability_id: str,
        observation_summary: dict[str, Any] | None,
        result_payload: dict[str, Any],
        created_at: str,
    ) -> list[dict[str, Any]]:
        existing = run.get("observed_result_summaries")
        summaries = [deepcopy(item) for item in existing] if isinstance(existing, list) else []
        tool_name = None
        summary_text = None
        if isinstance(observation_summary, dict):
            raw_tool = observation_summary.get("tool_name")
            if isinstance(raw_tool, str) and raw_tool.strip():
                tool_name = raw_tool.strip()
            raw_summary = observation_summary.get("mcp_result_summary")
            if isinstance(raw_summary, str) and raw_summary.strip():
                summary_text = raw_summary.strip()
        if summary_text is None:
            summary_text = self._capability_result_followup_hint_summary(
                capability_id=capability_id,
                observation_summary=observation_summary,
                result_payload=result_payload,
            )
        if not isinstance(summary_text, str) or not summary_text.strip():
            summary_text = f"{capability_id} の結果を受け取った。"
        summaries.append(
            {
                "capability_id": capability_id,
                "tool_name": tool_name,
                "summary_text": summary_text.strip(),
                "created_at": created_at,
            }
        )
        return summaries

    def _merge_autonomous_observed_persons(
        self,
        *,
        run: dict[str, Any],
        observation_summary: dict[str, Any] | None,
    ) -> list[dict[str, str]]:
        existing = run.get("observed_persons")
        merged: list[dict[str, str]] = []
        seen: set[str] = set()
        for source in (existing if isinstance(existing, list) else [], self._observed_persons_from_mcp_observation(observation_summary)):
            for item in source:
                if not isinstance(item, dict):
                    continue
                person_ref = item.get("person_ref")
                display_name = item.get("display_name")
                if not isinstance(person_ref, str) or not person_ref.startswith("person:"):
                    continue
                if not isinstance(display_name, str) or not display_name.strip():
                    continue
                if person_ref in seen:
                    continue
                seen.add(person_ref)
                merged.append(
                    {
                        "person_ref": person_ref,
                        "display_name": display_name.strip(),
                    }
                )
        return merged

    def _append_autonomous_result_events(
        self,
        *,
        run: dict[str, Any],
        result_event: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        existing = run.get("result_events")
        events = [deepcopy(item) for item in existing] if isinstance(existing, list) else []
        if isinstance(result_event, dict) and isinstance(result_event.get("event_id"), str):
            events.append(deepcopy(result_event))
        return events

    def _append_autonomous_result_history(
        self,
        *,
        run: dict[str, Any],
        capability_id: str,
        observation_summary: dict[str, Any] | None,
        result_payload: dict[str, Any],
    ) -> str:
        result_summary = self._capability_result_followup_hint_summary(
            capability_id=capability_id,
            observation_summary=observation_summary,
            result_payload=result_payload,
        )
        if result_summary is None:
            result_summary = f"{capability_id} の結果を受け取った。"
        existing = str(run.get("history_summary") or "").strip()
        merged = f"{existing} / result={result_summary}" if existing else f"result={result_summary}"
        return merged

    def _pause_autonomous_runs_for_user_interaction(
        self,
        *,
        state: dict[str, Any],
        current_time: str,
    ) -> list[str]:
        runs = self.store.list_autonomous_runs(
            memory_set_id=state["selected_memory_set_id"],
            statuses=["active", "waiting_timer"],
            limit=50,
        )
        paused_ids: list[str] = []
        for run in runs:
            paused = self._pause_autonomous_run_for_user_interaction(run=run, current_time=current_time)
            if isinstance(paused, dict):
                paused_ids.append(str(paused.get("run_id") or ""))
        return [run_id for run_id in paused_ids if run_id]

    def _pause_autonomous_run_for_user_interaction(
        self,
        *,
        run: dict[str, Any],
        current_time: str,
    ) -> dict[str, Any] | None:
        if run.get("status") not in {"active", "waiting_timer"}:
            return None
        paused = self._paused_autonomous_run(
            run=run,
            current_time=current_time,
            pause_reason="paused_by_user_interaction",
        )
        self.store.upsert_autonomous_run(autonomous_run=paused)
        return paused

    def _paused_autonomous_run(
        self,
        *,
        run: dict[str, Any],
        current_time: str,
        pause_reason: str,
    ) -> dict[str, Any]:
        return {
            **run,
            "status": "paused",
            "resume_status": run.get("status"),
            "pause_reason": pause_reason,
            "updated_at": current_time,
        }

    def _resume_autonomous_runs_after_user_interaction(
        self,
        *,
        state: dict[str, Any],
        current_time: str,
    ) -> list[str]:
        runs = self.store.list_autonomous_runs(
            memory_set_id=state["selected_memory_set_id"],
            statuses=["paused"],
            limit=50,
        )
        resumed: list[str] = []
        for run in runs:
            if run.get("pause_reason") != "paused_by_user_interaction":
                continue
            resume_status = str(run.get("resume_status") or "active").strip()
            if resume_status not in {"active", "waiting_timer"}:
                resume_status = "active"
            updated = {
                **run,
                "status": resume_status,
                "resume_status": None,
                "pause_reason": None,
                "updated_at": current_time,
            }
            self.store.upsert_autonomous_run(autonomous_run=updated)
            resumed.append(str(run.get("run_id") or ""))
        return [run_id for run_id in resumed if run_id]

    def _cancel_autonomous_runs_for_user_request(
        self,
        *,
        state: dict[str, Any],
        current_time: str,
    ) -> list[str]:
        runs = self.store.list_autonomous_runs(
            memory_set_id=state["selected_memory_set_id"],
            statuses=sorted(AUTONOMOUS_RUN_ACTIVE_STATUSES),
            limit=50,
        )
        cancelled: list[str] = []
        for run in runs:
            updated = self._terminal_autonomous_run(
                run=run,
                current_time=current_time,
                status="cancelled",
                reason_summary="conversation API の autonomous_run_action=cancel_all により cancel した。",
            )
            self.store.upsert_autonomous_run(autonomous_run=updated)
            updated = self._finalize_autonomous_run_commitments(
                state=state,
                run=updated,
                terminal_status="cancelled",
                current_time=current_time,
                evidence_events=[],
            )
            cancelled.append(str(run.get("run_id") or ""))
        return [run_id for run_id in cancelled if run_id]

    def _link_autonomous_run_source_commitments(
        self,
        *,
        state: dict[str, Any],
        pipeline: dict[str, Any],
        memory_trace: dict[str, Any] | None,
        current_time: str,
    ) -> None:
        if not isinstance(memory_trace, dict) or memory_trace.get("turn_consolidation_status") != "succeeded":
            return
        decision = pipeline.get("decision")
        if not isinstance(decision, dict):
            return
        self_decision = self._execution_self_decision(decision)
        if self_decision.get("kind") != "autonomous_run":
            return
        run_summary = pipeline.get("autonomous_run_summary")
        run_id = run_summary.get("run_id") if isinstance(run_summary, dict) else None
        if not isinstance(run_id, str) or not run_id.strip():
            return
        updated_memory_unit_ids = [
            value
            for value in memory_trace.get("updated_memory_unit_ids", [])
            if isinstance(value, str) and value.strip()
        ]
        if not updated_memory_unit_ids:
            return

        memory_set_id = str(state.get("selected_memory_set_id") or "").strip()
        units = self.store.list_memory_units_by_id(
            memory_set_id=memory_set_id,
            memory_unit_ids=updated_memory_unit_ids,
        )
        commitment_ids = [
            unit["memory_unit_id"]
            for unit in units
            if unit.get("memory_type") == "commitment"
            and unit.get("commitment_state") in {"open", "waiting_confirmation", "on_hold"}
            and isinstance(unit.get("memory_unit_id"), str)
        ]
        if not commitment_ids:
            return

        run = self.store.get_autonomous_run(run_id=run_id)
        if not isinstance(run, dict):
            return
        existing_ids = [
            value
            for value in run.get("source_commitment_memory_unit_ids", [])
            if isinstance(value, str) and value.strip()
        ]
        merged_ids = [*existing_ids]
        for commitment_id in commitment_ids:
            if commitment_id not in merged_ids:
                merged_ids.append(commitment_id)
        if merged_ids == existing_ids:
            return

        updated = {
            **run,
            "source_commitment_memory_unit_ids": merged_ids,
            "updated_at": current_time,
        }
        self.store.upsert_autonomous_run(autonomous_run=updated)
        if updated.get("status") in AUTONOMOUS_RUN_TERMINAL_STATUSES:
            updated = self._finalize_autonomous_run_commitments(
                state=state,
                run=updated,
                terminal_status=str(updated.get("status") or ""),
                current_time=current_time,
                evidence_events=[],
            )
    def _persist_autonomous_run_speech_event(
        self,
        *,
        run: dict[str, Any],
        speech_payload: dict[str, Any],
        created_at: str,
        step: dict[str, Any],
        transition: dict[str, Any],
    ) -> dict[str, Any]:
        event = {
            "event_id": f"event:{uuid.uuid4().hex}",
            "cycle_id": self._autonomous_run_event_cycle_id(run),
            "memory_set_id": run["memory_set_id"],
            "kind": "speech",
            "role": "assistant",
            "text": speech_payload["speech_text"],
            "created_at": created_at,
            "source_kind": "autonomous_run",
            "run_id": run.get("run_id"),
            "transition_kind": transition.get("kind"),
            "reason_summary": self._autonomous_step_reason_summary(
                step,
                fallback="autonomous_run が発話した。",
            ),
        }
        self.store.append_events(events=[event])
        return event

    def _record_autonomous_pre_send_check_terminal(
        self,
        *,
        run: dict[str, Any],
        current_time: str,
        reason_code: str,
        audit_summary: dict[str, Any],
        message: str,
    ) -> None:
        interaction_ref = run.get("origin_interaction_ref")
        participant_refs = run.get("participant_refs")
        conversation_visible = (
            isinstance(interaction_ref, str)
            and bool(interaction_ref)
            and isinstance(participant_refs, list)
            and bool(participant_refs)
        )
        normalized_participants = participant_refs if isinstance(participant_refs, list) else []
        notice = {
            "source_kind": "pre_send_check",
            "code": reason_code,
            "message": message,
            "conversation_visible": conversation_visible,
            "interaction_ref": interaction_ref if isinstance(interaction_ref, str) else None,
            "recipient_person_refs": normalized_participants,
        }
        self._broadcast_system_notice(notice)
        events = [
            {
                "event_id": f"event:{uuid.uuid4().hex}",
                "cycle_id": self._autonomous_run_event_cycle_id(run),
                "memory_set_id": run["memory_set_id"],
                "kind": "pre_send_check",
                "role": "system",
                "text": None,
                "created_at": current_time,
                "source_kind": "autonomous_run",
                "run_id": run.get("run_id"),
                "pre_send_check": deepcopy(audit_summary),
            }
        ]
        if conversation_visible:
            events.append(
                {
                    "event_id": f"event:{uuid.uuid4().hex}",
                    "cycle_id": self._autonomous_run_event_cycle_id(run),
                    "memory_set_id": run["memory_set_id"],
                    "kind": "system_notice",
                    "role": "system",
                    "text": message,
                    "code": reason_code,
                    "interaction_ref": interaction_ref,
                    "speaker_ref": None,
                    "participant_refs": normalized_participants,
                    "created_at": current_time,
                    "source_kind": "autonomous_run",
                    "run_id": run.get("run_id"),
                }
            )
        self.store.append_events(events=events)

    def _persist_autonomous_pre_send_check_audit(
        self,
        *,
        run: dict[str, Any],
        current_time: str,
        audit_summary: dict[str, Any],
    ) -> None:
        self.store.append_events(
            events=[
                {
                    "event_id": f"event:{uuid.uuid4().hex}",
                    "cycle_id": self._autonomous_run_event_cycle_id(run),
                    "memory_set_id": run["memory_set_id"],
                    "kind": "pre_send_check",
                    "role": "system",
                    "text": None,
                    "created_at": current_time,
                    "source_kind": "autonomous_run",
                    "run_id": run.get("run_id"),
                    "pre_send_check": deepcopy(audit_summary),
                }
            ]
        )

    def _append_autonomous_run_terminal_event(
        self,
        *,
        run: dict[str, Any],
        terminal_status: str,
        current_time: str,
    ) -> dict[str, Any]:
        event = {
            "event_id": f"event:{uuid.uuid4().hex}",
            "cycle_id": self._autonomous_run_event_cycle_id(run),
            "memory_set_id": run["memory_set_id"],
            "kind": "autonomous_run_terminal",
            "role": "system",
            "text": None,
            "created_at": current_time,
            "source_kind": "autonomous_run",
            "run_id": run.get("run_id"),
            "terminal_status": terminal_status,
            "objective_summary": run.get("objective_summary"),
            "history_summary": run.get("history_summary"),
            "observed_result_summaries": run.get("observed_result_summaries") or [],
            "observed_persons": run.get("observed_persons") or [],
            "observed_person_refs": [
                person["person_ref"]
                for person in (run.get("observed_persons") or [])
                if isinstance(person, dict) and isinstance(person.get("person_ref"), str)
            ],
            "last_result_context": run.get("last_result_context"),
        }
        self.store.append_events(events=[event])
        return event

    def _finalize_autonomous_run_commitments(
        self,
        *,
        state: dict[str, Any],
        run: dict[str, Any],
        terminal_status: str,
        current_time: str,
        evidence_events: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if terminal_status not in AUTONOMOUS_RUN_TERMINAL_STATUSES:
            return run
        existing_resolution = run.get("commitment_resolution")
        if (
            isinstance(existing_resolution, dict)
            and existing_resolution.get("terminal_status") == terminal_status
            and (
                existing_resolution.get("result_status") == "updated"
                or (
                    existing_resolution.get("result_status") == "skipped"
                    and existing_resolution.get("reason") != "no_linked_commitments"
                )
            )
        ):
            return run

        terminal_event = self._append_autonomous_run_terminal_event(
            run=run,
            terminal_status=terminal_status,
            current_time=current_time,
        )
        evidence_event_ids = [
            event["event_id"]
            for event in [*evidence_events, terminal_event]
            if isinstance(event.get("event_id"), str)
        ]
        try:
            resolution = self.memory.resolve_autonomous_run_commitments(
                state=state,
                run=run,
                terminal_status=terminal_status,
                finished_at=current_time,
                evidence_event_ids=evidence_event_ids,
            )
        except Exception as exc:  # noqa: BLE001
            resolution = {
                "result_status": "failed",
                "reason": str(exc),
                "terminal_status": terminal_status,
                "target_commitment_state": None,
                "updated_memory_unit_ids": [],
                "memory_action_count": 0,
                "memory_link_update": None,
                "entity_registry_update": None,
                "vector_index_sync": {
                    "result_status": "not_started",
                    "failure_reason": None,
                },
                "relation_index_sync": self._relation_index_sync_trace("not_started"),
            }
            debug_log(
                "AutonomousRun",
                f"commitment resolution failed run={run.get('run_id')} error={type(exc).__name__}: {self._clamp(str(exc))}",
                level="ERROR",
            )

        relation_index_sync = resolution.get("relation_index_sync")
        if (
            isinstance(relation_index_sync, dict)
            and relation_index_sync.get("result_status") == "failed"
        ):
            self.store.append_events(
                events=[
                    self._build_memory_audit_event(
                        cycle_id=self._autonomous_run_event_cycle_id(run),
                        memory_set_id=run["memory_set_id"],
                        kind="relation_index_sync_failure",
                        created_at=current_time,
                        payload={
                            "failure_reason": relation_index_sync.get("failure_reason"),
                            "autonomous_run_id": run.get("run_id"),
                        },
                    )
                ]
            )

        updated = {
            **run,
            "commitment_resolution": {
                **resolution,
                "terminal_status": terminal_status,
                "evidence_event_ids": evidence_event_ids,
                "updated_at": current_time,
            },
            "updated_at": current_time,
        }
        self.store.upsert_autonomous_run(autonomous_run=updated)
        return self._consolidate_autonomous_run_terminal(
            state=state,
            run=updated,
            terminal_status=terminal_status,
            current_time=current_time,
            evidence_events=evidence_events,
            terminal_event=terminal_event,
        )

    def _consolidate_autonomous_run_terminal(
        self,
        *,
        state: dict[str, Any],
        run: dict[str, Any],
        terminal_status: str,
        current_time: str,
        evidence_events: list[dict[str, Any]],
        terminal_event: dict[str, Any],
    ) -> dict[str, Any]:
        existing = run.get("terminal_consolidation")
        if isinstance(existing, dict) and existing.get("result_status") in {"succeeded", "failed"}:
            return run
        cycle_id = self._new_cycle_id()
        input_text = self._autonomous_run_terminal_memory_input_text(
            run=run,
            terminal_status=terminal_status,
            evidence_events=evidence_events,
        )
        speech_payload = None
        speech_text = None
        for event in evidence_events:
            if isinstance(event, dict) and event.get("kind") == "speech":
                text = event.get("text")
                if isinstance(text, str) and text.strip():
                    speech_text = text.strip()
        if speech_text is not None:
            speech_payload = {"speech_text": speech_text}
        source_current_input = run.get("source_current_input")
        interaction_context = None
        if isinstance(source_current_input, dict):
            try:
                interaction_context = normalize_interaction_context(
                    source_current_input.get("interaction_context")
                )
            except (TypeError, ValueError):
                interaction_context = None
        participant_refs = tuple(
            ref
            for ref in (run.get("participant_refs") or [])
            if isinstance(ref, str) and ref.startswith("person:")
        )
        current_input = CurrentInput(
            sender_kind="system",
            sender_ref=None,
            source_kind="autonomous_run",
            response_target_refs=participant_refs,
            interaction_context=interaction_context,
            text=input_text,
        )
        people_context = self._build_people_context(
            state=state,
            current_input=current_input,
            structured_sources=[run, run.get("last_result_context")],
        )
        events = [
            *[
                deepcopy(event)
                for event in (run.get("result_events") or [])
                if isinstance(event, dict)
            ],
            *[deepcopy(event) for event in evidence_events if isinstance(event, dict)],
            deepcopy(terminal_event),
        ]
        decision = {
            "kind": "speech" if speech_payload is not None else "noop",
            "reason_code": f"autonomous_run_{terminal_status}",
            "reason_summary": str(run.get("current_step_summary") or run.get("history_summary") or "autonomous_run が終了した。"),
        }
        recall_hint = {
            **self._empty_recall_hint(),
            "primary_recall_focus": "episodic",
            "time_reference": "recent",
            "mentioned_entities": [
                person["person_ref"]
                for person in people_context
                if isinstance(person, dict) and isinstance(person.get("person_ref"), str)
            ][:4],
        }
        cycle_summary = self._build_cycle_summary(
            cycle_id=cycle_id,
            started_at=current_time,
            finished_at=current_time,
            state=state,
            trigger_kind="autonomous_run",
            result_kind=decision["kind"],
            failed=False,
            input_text=input_text,
            decision=decision,
            speech_payload=speech_payload,
        )
        retrieval_run = {
            "cycle_id": cycle_id,
            "selected_memory_set_id": state["selected_memory_set_id"],
            "started_at": current_time,
            "finished_at": current_time,
            "result_status": "skipped",
        }
        cycle_trace = {
            "cycle_id": cycle_id,
            "cycle_summary": cycle_summary,
            "input_trace": {
                "trigger_kind": "autonomous_run",
                "run_id": run.get("run_id"),
                "terminal_status": terminal_status,
            },
        }
        try:
            self.store.persist_cycle_records(
                events=[],
                retrieval_run=retrieval_run,
                cycle_summary=cycle_summary,
                cycle_trace=cycle_trace,
            )
            memory_trace = self._finalize_memory_trace(
                cycle_id=cycle_id,
                finished_at=current_time,
                state=state,
                input_text=input_text,
                events=events,
                pipeline={
                    "recall_hint": recall_hint,
                    "decision": decision,
                    "speech_payload": speech_payload,
                    "current_input": current_input.to_prompt_payload(),
                    "people_context": people_context,
                },
                trigger_kind="autonomous_run",
                input_event_kind="autonomous_run_terminal",
                input_event_role="system",
            )
            consolidation = {
                "result_status": "succeeded",
                "cycle_id": cycle_id,
                "episode_id": memory_trace.get("episode_id") if isinstance(memory_trace, dict) else None,
                "updated_at": current_time,
            }
        except Exception as exc:  # noqa: BLE001
            debug_log(
                "AutonomousRun",
                f"terminal consolidation failed run={run.get('run_id')} error={type(exc).__name__}: {self._clamp(str(exc))}",
                level="ERROR",
            )
            consolidation = {
                "result_status": "failed",
                "cycle_id": cycle_id,
                "failure_reason": str(exc),
                "updated_at": current_time,
            }
        updated = {
            **run,
            "terminal_consolidation": consolidation,
            "updated_at": current_time,
        }
        self.store.upsert_autonomous_run(autonomous_run=updated)
        debug_log(
            "AutonomousRun",
            (
                f"terminal consolidation run={run.get('run_id')} "
                f"status={consolidation.get('result_status')} "
                f"cycle={self._short_cycle_id(cycle_id)}"
            ),
        )
        return updated

    def _autonomous_run_terminal_memory_input_text(
        self,
        *,
        run: dict[str, Any],
        terminal_status: str,
        evidence_events: list[dict[str, Any]],
    ) -> str:
        parts = [
            f"autonomous_run が {terminal_status} になった。",
        ]
        objective = run.get("objective_summary")
        if isinstance(objective, str) and objective.strip():
            parts.append(f"目的は {objective.strip()}。")
        history = run.get("history_summary")
        if isinstance(history, str) and history.strip():
            parts.append(f"実行履歴は {history.strip()}。")
        observed_summaries = run.get("observed_result_summaries")
        if isinstance(observed_summaries, list):
            for item in observed_summaries:
                if not isinstance(item, dict):
                    continue
                tool_name = item.get("tool_name")
                summary_text = item.get("summary_text")
                if isinstance(summary_text, str) and summary_text.strip():
                    if isinstance(tool_name, str) and tool_name.strip():
                        parts.append(f"{tool_name.strip()} の結果は {summary_text.strip()}")
                    else:
                        parts.append(summary_text.strip())
        for event in evidence_events:
            if not isinstance(event, dict) or event.get("kind") != "speech":
                continue
            text = event.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(f"外向き発話は {text.strip()}")
        observed_persons = run.get("observed_persons")
        if isinstance(observed_persons, list) and observed_persons:
            names = [
                f"{item['display_name']} ({item['person_ref']})"
                for item in observed_persons
                if isinstance(item, dict)
                and isinstance(item.get("display_name"), str)
                and isinstance(item.get("person_ref"), str)
            ]
            if names:
                parts.append("観測した人物は " + "、".join(names) + "。")
        return " ".join(parts)

    def _autonomous_run_event_cycle_id(self, run: dict[str, Any]) -> str:
        source_cycle_id = run.get("source_cycle_id")
        if isinstance(source_cycle_id, str) and source_cycle_id.strip():
            return source_cycle_id.strip()
        return f"cycle:{uuid.uuid4().hex}"

    def _emit_autonomous_run_assistant_message_event(
        self,
        *,
        state: dict[str, Any],
        run: dict[str, Any],
        speech_payload: dict[str, Any],
    ) -> None:
        interaction_ref = run.get("origin_interaction_ref")
        participant_refs = run.get("participant_refs")
        if (
            not isinstance(interaction_ref, str)
            or not interaction_ref
            or not isinstance(participant_refs, list)
            or not participant_refs
        ):
            return
        persona_id = state["selected_persona_id"]
        persona = state["personas"][persona_id]
        sent, _ = self._emit_assistant_message_with_audio(
            event_data={
                "cycle_id": self._autonomous_run_event_cycle_id(run),
                "source_kind": "autonomous_run",
                "run_id": run.get("run_id"),
                "persona_id": persona_id,
                "persona_display_name": persona["display_name"],
                "interaction_ref": interaction_ref,
                "recipient_person_refs": participant_refs,
                "system_text": "[autonomous_run]",
            },
            speech_text=speech_payload["speech_text"],
        )
        debug_log(
            "AutonomousRun",
            (
                f"assistant_message sent={sent} run={run.get('run_id')} "
                f"speech_chars={len(speech_payload['speech_text'])}"
            ),
            level="DEBUG",
        )

    def _request_run_assistant_message_target_client_id(self, run: dict[str, Any]) -> str | None:
        return self._normalize_capability_client_id(run.get("assistant_message_target_client_id"))

    def _list_autonomous_run_prompt_summaries(
        self,
        *,
        state: dict[str, Any],
        current_time: str,
    ) -> list[dict[str, Any]]:
        _ = current_time
        runs = self.store.list_autonomous_runs(
            memory_set_id=state["selected_memory_set_id"],
            statuses=sorted(AUTONOMOUS_RUN_ACTIVE_STATUSES),
            limit=20,
        )
        return [self._autonomous_run_prompt_summary(run) for run in runs]

    def _autonomous_run_prompt_summary(self, run: dict[str, Any]) -> dict[str, Any]:
        summary = {
            "run_id": run.get("run_id"),
            "status": run.get("status"),
            "objective_summary": run.get("objective_summary"),
            "origin_kind": run.get("origin_kind"),
            "current_step_summary": run.get("current_step_summary"),
            "history_summary": run.get("history_summary"),
            "next_run_at": run.get("next_run_at"),
            "waiting_request_id": run.get("waiting_request_id"),
            "pause_reason": run.get("pause_reason"),
            "resume_status": run.get("resume_status"),
            "created_at": run.get("created_at"),
            "updated_at": run.get("updated_at"),
        }
        self._attach_autonomous_observation_summary(summary, run)
        return summary

    def _autonomous_run_public_summary(self, run: dict[str, Any], *, current_time: str) -> dict[str, Any]:
        _ = current_time
        summary = {
            "run_id": run.get("run_id"),
            "memory_set_id": run.get("memory_set_id"),
            "status": run.get("status"),
            "objective_summary": run.get("objective_summary"),
            "origin_kind": run.get("origin_kind"),
            "current_step_summary": run.get("current_step_summary"),
            "history_summary": run.get("history_summary"),
            "next_run_at": run.get("next_run_at"),
            "waiting_request_id": run.get("waiting_request_id"),
            "pause_reason": run.get("pause_reason"),
            "resume_status": run.get("resume_status"),
            "coordination": run.get("coordination"),
            "created_at": run.get("created_at"),
            "updated_at": run.get("updated_at"),
            "completed_at": run.get("completed_at"),
        }
        self._attach_autonomous_observation_summary(summary, run)
        return summary

    def _attach_autonomous_observation_summary(self, summary: dict[str, Any], run: dict[str, Any]) -> None:
        observed_summaries = run.get("observed_result_summaries")
        if isinstance(observed_summaries, list) and observed_summaries:
            summary["observed_result_summaries"] = deepcopy(observed_summaries)
        observed_persons = run.get("observed_persons")
        if isinstance(observed_persons, list) and observed_persons:
            summary["observed_persons"] = deepcopy(observed_persons)
            summary["observed_person_refs"] = [
                person["person_ref"]
                for person in observed_persons
                if isinstance(person, dict) and isinstance(person.get("person_ref"), str)
            ]
