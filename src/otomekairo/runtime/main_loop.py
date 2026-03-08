"""Minimal runtime loop for consuming pending inputs."""

from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from otomekairo.gateway.camera_controller import CameraController
from otomekairo.gateway.camera_sensor import CameraSensor
from otomekairo import __version__
from otomekairo.gateway.cognition_client import CognitionClient
from otomekairo.gateway.search_client import SearchClient
from otomekairo.gateway.speech_synthesizer import SpeechSynthesizer
from otomekairo.infra.sqlite_state_store import SqliteStateStore
from otomekairo.schema.runtime_types import (
    ActionHistoryRecord,
    MemoryJobRecord,
    PendingInputRecord,
    PendingInputMutationRecord,
    SettingsOverrideRecord,
    TaskStateRecord,
    TaskStateMutationRecord,
)
from otomekairo.schema.settings import SettingsValidationError, build_default_settings, decode_requested_value, get_setting_definition
from otomekairo.usecase.build_cognition_input import build_cognition_input
from otomekairo.usecase.observation_normalization import normalize_trigger_reason
from otomekairo.usecase.run_browse_task import run_browse_task
from otomekairo.usecase.run_cognition import run_cognition_for_browser_chat_input


# Block: Runtime constants
DEFAULT_LEASE_HEARTBEAT_MS = 5_000
MINIMUM_LEASE_TTL_MS = 15_000
DEFAULT_LEASE_TTL_MS = 60_000
DEFAULT_RUNTIME_WAIT_POLL_MS = 100
MAX_MEMORY_JOB_TRIES = 3
PENDING_INPUT_FAILURE_REASON = "processing_failed"
SETTINGS_OVERRIDE_FAILURE_REASON = "settings_processing_failed"
SETTINGS_CHANGE_SET_FAILURE_REASON = "settings_editor_processing_failed"
CANCEL_FAILURE_REASON = "cancel_processing_failed"


# Block: Module logger
logger = logging.getLogger(__name__)


# Block: Runtime loop
class RuntimeLoop:
    def __init__(
        self,
        *,
        store: SqliteStateStore,
        owner_token: str,
        default_settings: dict[str, Any],
        cognition_client: CognitionClient,
        search_client: SearchClient,
        camera_controller: CameraController,
        camera_sensor: CameraSensor,
        speech_synthesizer: SpeechSynthesizer,
        lease_heartbeat_ms: int = DEFAULT_LEASE_HEARTBEAT_MS,
        lease_ttl_ms: int = DEFAULT_LEASE_TTL_MS,
    ) -> None:
        # Block: Lease parameter validation
        if lease_heartbeat_ms <= 0:
            raise RuntimeError("lease_heartbeat_ms must be positive")
        if lease_heartbeat_ms > DEFAULT_LEASE_HEARTBEAT_MS:
            raise RuntimeError("lease_heartbeat_ms must be 5000 or less")
        if lease_ttl_ms < MINIMUM_LEASE_TTL_MS:
            raise RuntimeError("lease_ttl_ms must be 15000 or more")
        self._store = store
        self._owner_token = owner_token
        self._default_settings = default_settings
        self._cognition_client = cognition_client
        self._search_client = search_client
        self._camera_controller = camera_controller
        self._camera_sensor = camera_sensor
        self._speech_synthesizer = speech_synthesizer
        self._lease_heartbeat_ms = lease_heartbeat_ms
        self._lease_ttl_ms = lease_ttl_ms
        self._boot_reconciled = False
        self._prefer_long_cycle = False
        self._last_long_cycle_at_ms = 0
        self._last_activity_at_ms = _now_ms()
        self._last_lease_refresh_at_ms = 0
        self._stop_requested = False
        logger.info(
            "runtime loop initialized",
            extra={
                "owner_token": owner_token,
                "lease_heartbeat_ms": lease_heartbeat_ms,
                "lease_ttl_ms": lease_ttl_ms,
            },
        )

    # Block: Single iteration
    def run_once(self) -> bool:
        self._refresh_runtime_lease()
        if not self._boot_reconciled:
            self._store.materialize_next_boot_settings()
            self._boot_reconciled = True
            logger.info("runtime boot settings materialized")
        replayed_commit_logs = self._store.sync_pending_commit_logs(max_commits=4)
        did_replay_commit_logs = replayed_commit_logs > 0
        if replayed_commit_logs > 0:
            self._mark_runtime_activity()
            logger.debug(
                "commit logs replayed",
                extra={"replayed_commit_logs": replayed_commit_logs},
            )
        if self._prefer_long_cycle:
            processed_memory = self._process_memory_job_once()
            if processed_memory:
                self._prefer_long_cycle = False
                self._mark_runtime_activity()
                return True
        processed_editor_settings = self._process_settings_change_set_once()
        if processed_editor_settings:
            self._prefer_long_cycle = True
            self._mark_runtime_activity()
            return True
        processed_settings = self._process_settings_override_once()
        if processed_settings:
            self._prefer_long_cycle = True
            self._mark_runtime_activity()
            return True
        pending_input = self._store.claim_next_pending_input()
        if pending_input is not None:
            logger.info(
                "claimed pending input",
                extra={
                    "input_id": pending_input.input_id,
                    "input_kind": pending_input.payload["input_kind"],
                    "channel": pending_input.channel,
                },
            )
            self._process_claimed_pending_input(pending_input)
            self._prefer_long_cycle = True
            self._mark_runtime_activity()
            return True
        waiting_task = self._store.claim_next_waiting_browse_task()
        if waiting_task is not None:
            logger.info(
                "claimed waiting task",
                extra={
                    "task_id": waiting_task.task_id,
                    "task_kind": waiting_task.task_kind,
                    "task_status": waiting_task.task_status,
                },
            )
            self._process_claimed_waiting_task(waiting_task)
            self._prefer_long_cycle = True
            self._mark_runtime_activity()
            return True
        processed_memory = self._process_memory_job_once()
        if processed_memory:
            self._mark_runtime_activity()
            return True
        pending_input = self._claim_idle_tick_pending_input_if_due()
        if pending_input is not None:
            logger.info(
                "claimed pending input",
                extra={
                    "input_id": pending_input.input_id,
                    "input_kind": pending_input.payload["input_kind"],
                    "channel": pending_input.channel,
                },
            )
            self._process_claimed_pending_input(pending_input)
            self._prefer_long_cycle = True
            self._mark_runtime_activity()
            return True
        if did_replay_commit_logs:
            return True
        return False

    # Block: Claimed pending input processing
    def _process_claimed_pending_input(self, pending_input: PendingInputRecord) -> None:
        cycle_id = _opaque_id("cycle")
        try:
            self._store.append_input_journal_for_pending_input(
                pending_input=pending_input,
                cycle_id=cycle_id,
            )
            resolved_at = _now_ms()
            (
                ui_events,
                action_results,
                task_mutations,
                pending_input_mutations,
                resolution_status,
                discard_reason,
                retrieval_run,
                attention_snapshot,
            ) = self._resolve_pending_input(
                pending_input=pending_input,
                cycle_id=cycle_id,
                resolved_at=resolved_at,
            )
            commit_payload = {
                "cycle_kind": "short",
                "trigger_reason": _pending_input_trigger_reason(pending_input),
                "processed_input_id": pending_input.input_id,
                "processed_input_kind": pending_input.payload["input_kind"],
                "emitted_event_types": [ui_event["event_type"] for ui_event in ui_events],
                "executed_action_types": [action_result.action_type for action_result in action_results],
                "resolution_status": resolution_status,
                **(
                    {"attention_primary_focus": attention_snapshot["primary_focus"]["summary"]}
                    if attention_snapshot is not None
                    else {}
                ),
            }
            self._store.finalize_pending_input_cycle(
                pending_input=pending_input,
                cycle_id=cycle_id,
                resolution_status=resolution_status,
                action_results=action_results,
                task_mutations=task_mutations,
                pending_input_mutations=pending_input_mutations,
                discard_reason=discard_reason,
                ui_events=ui_events,
                retrieval_run=retrieval_run,
                attention_snapshot=attention_snapshot,
                commit_payload=commit_payload,
                camera_available=self._camera_available(),
            )
            logger.info(
                "pending input finalized",
                extra={
                    "cycle_id": cycle_id,
                    "input_id": pending_input.input_id,
                    "input_kind": pending_input.payload["input_kind"],
                    "resolution_status": resolution_status,
                    "executed_action_types": [
                        action_result.action_type for action_result in action_results
                    ],
                    "emitted_event_types": [ui_event["event_type"] for ui_event in ui_events],
                    "followup_input_count": len(pending_input_mutations),
                },
            )
        except Exception as error:
            logger.exception("pending input processing failed: input_id=%s", pending_input.input_id)
            ui_events = _failed_pending_input_events(
                pending_input=pending_input,
                error=error,
            )
            self._store.finalize_pending_input_cycle(
                pending_input=pending_input,
                cycle_id=cycle_id,
                resolution_status="discarded",
                action_results=[],
                task_mutations=[],
                pending_input_mutations=[],
                discard_reason=PENDING_INPUT_FAILURE_REASON,
                ui_events=ui_events,
                attention_snapshot=None,
                commit_payload={
                    "cycle_kind": "short",
                    "trigger_reason": _pending_input_trigger_reason(pending_input),
                    "processed_input_id": pending_input.input_id,
                    "processed_input_kind": pending_input.payload["input_kind"],
                    "emitted_event_types": [ui_event["event_type"] for ui_event in ui_events],
                    "executed_action_types": [],
                    "resolution_status": "discarded",
                    "error_kind": type(error).__name__,
                    "error_message": _error_message_text(error),
                },
                camera_available=self._camera_available(),
            )

    # Block: Claimed waiting task processing
    def _process_claimed_waiting_task(self, task: TaskStateRecord) -> None:
        cycle_id = _opaque_id("cycle")
        try:
            execution = run_browse_task(
                task=task,
                cycle_id=cycle_id,
                search_client=self._search_client,
            )
            self._store.finalize_task_cycle(
                task=task,
                cycle_id=cycle_id,
                final_status=execution.final_status,
                action_results=execution.action_results,
                pending_input_mutations=execution.pending_input_mutations,
                ui_events=execution.ui_events,
                commit_payload={
                    "cycle_kind": "short",
                    "trigger_reason": "task_resume",
                    "processed_task_id": task.task_id,
                    "processed_task_kind": task.task_kind,
                    "emitted_event_types": [
                        ui_event["event_type"] for ui_event in execution.ui_events
                    ],
                    "executed_action_types": [
                        action_result.action_type for action_result in execution.action_results
                    ],
                    "final_task_status": execution.final_status,
                },
                camera_available=self._camera_available(),
            )
            logger.info(
                "waiting task finalized",
                extra={
                    "cycle_id": cycle_id,
                    "task_id": task.task_id,
                    "task_kind": task.task_kind,
                    "final_status": execution.final_status,
                    "executed_action_types": [
                        action_result.action_type for action_result in execution.action_results
                    ],
                },
            )
        except Exception as error:
            logger.exception("waiting task processing failed: task_id=%s", task.task_id)
            ui_events = _failed_task_events(task=task, cycle_id=cycle_id)
            self._append_ui_events(cycle_id=cycle_id, ui_events=ui_events)
            failed_action = _failed_task_action_result(
                task=task,
                cycle_id=cycle_id,
                error=error,
            )
            self._store.finalize_task_cycle(
                task=task,
                cycle_id=cycle_id,
                final_status="abandoned",
                action_results=[failed_action],
                pending_input_mutations=[],
                ui_events=ui_events,
                commit_payload={
                    "cycle_kind": "short",
                    "trigger_reason": "task_resume",
                    "processed_task_id": task.task_id,
                    "processed_task_kind": task.task_kind,
                    "emitted_event_types": [ui_event["event_type"] for ui_event in ui_events],
                    "executed_action_types": [failed_action.action_type],
                    "final_task_status": "abandoned",
                    "error_kind": type(error).__name__,
                    "error_message": _error_message_text(error),
                },
                camera_available=self._camera_available(),
            )

    # Block: Pending input resolution
    def _resolve_pending_input(
        self,
        *,
        pending_input: PendingInputRecord,
        cycle_id: str,
        resolved_at: int,
    ) -> tuple[
        list[dict[str, Any]],
        list[ActionHistoryRecord],
        list[TaskStateMutationRecord],
        list[PendingInputMutationRecord],
        str,
        str | None,
        dict[str, Any] | None,
        dict[str, Any] | None,
    ]:
        input_kind = pending_input.payload["input_kind"]
        if input_kind in {"chat_message", "microphone_message", "camera_observation", "network_result", "idle_tick"}:
            state_snapshot = self._store.read_cognition_state(
                self._default_settings,
                observation_hint_text=_pending_input_observation_hint(pending_input),
            )
            # Block: Camera candidate resolution
            camera_candidates = self._camera_candidates_for_state(state_snapshot.effective_settings)
            built_input = build_cognition_input(
                pending_input=pending_input,
                cycle_id=cycle_id,
                resolved_at=resolved_at,
                state_snapshot=state_snapshot,
                cognition_client=self._cognition_client,
                camera_candidates=camera_candidates,
                camera_available=bool(camera_candidates) and self._camera_sensor.is_available(),
            )
            cognition_execution = run_cognition_for_browser_chat_input(
                pending_input=pending_input,
                cycle_id=cycle_id,
                resolved_at=resolved_at,
                cognition_input=built_input.cognition_input,
                effective_settings=state_snapshot.effective_settings,
                cognition_client=self._cognition_client,
                camera_controller=self._camera_controller,
                camera_sensor=self._camera_sensor,
                speech_synthesizer=self._speech_synthesizer,
                emit_ui_event=lambda ui_event: self._append_ui_event(cycle_id=cycle_id, ui_event=ui_event),
                consume_cancel=lambda message_id: self._consume_matching_cancel(
                    channel=pending_input.channel,
                    message_id=message_id,
                ),
            )
            return (
                cognition_execution.ui_events,
                cognition_execution.action_results,
                cognition_execution.task_mutations,
                cognition_execution.pending_input_mutations,
                "consumed",
                None,
                built_input.retrieval_run,
                built_input.cognition_input["attention_snapshot"],
            )
        if input_kind == "cancel":
            return ([], [], [], [], "discarded", "cancel_target_not_found", None, None)
        ui_events, action_results = _unsupported_input_events(pending_input, resolved_at)
        self._append_ui_events(cycle_id=cycle_id, ui_events=ui_events)
        return (
            ui_events,
            action_results,
            [],
            [],
            "discarded",
            "unsupported_input_kind",
            None,
            None,
        )

    # Block: UI event append
    def _append_ui_event(self, *, cycle_id: str, ui_event: dict[str, Any]) -> None:
        self._refresh_runtime_lease()
        self._store.append_ui_outbound_event(
            channel=ui_event["channel"],
            event_type=ui_event["event_type"],
            payload=ui_event["payload"],
            source_cycle_id=cycle_id,
        )

    def _append_ui_events(self, *, cycle_id: str, ui_events: list[dict[str, Any]]) -> None:
        for ui_event in ui_events:
            self._append_ui_event(cycle_id=cycle_id, ui_event=ui_event)

    # Block: Active cancel handling
    def _consume_matching_cancel(self, *, channel: str, message_id: str) -> bool:
        self._refresh_runtime_lease()
        pending_input = self._store.claim_matching_cancel_input(
            channel=channel,
            target_message_id=message_id,
        )
        if pending_input is None:
            return False
        cycle_id = _opaque_id("cycle")
        try:
            resolved_at = _now_ms()
            self._store.append_input_journal_for_pending_input(
                pending_input=pending_input,
                cycle_id=cycle_id,
            )
            action_result = ActionHistoryRecord(
                result_id=_opaque_id("actres"),
                command_id=_opaque_id("cmd"),
                action_type="stop_active_message",
                command={
                    "target_channel": channel,
                    "target_message_id": message_id,
                    "event_types": [],
                },
                started_at=resolved_at,
                finished_at=resolved_at,
                status="succeeded",
                failure_mode=None,
                observed_effects={
                    "target_message_id": message_id,
                    "stop_reason": "cancel_requested",
                },
                raw_result_ref=None,
                adapter_trace_ref=None,
            )
            self._store.finalize_pending_input_cycle(
                pending_input=pending_input,
                cycle_id=cycle_id,
                resolution_status="consumed",
                action_results=[action_result],
                task_mutations=[],
                pending_input_mutations=[],
                discard_reason=None,
                ui_events=[],
                commit_payload={
                    "cycle_kind": "short",
                    "trigger_reason": "external_input",
                    "processed_input_id": pending_input.input_id,
                    "processed_input_kind": "cancel",
                    "emitted_event_types": [],
                    "executed_action_types": ["stop_active_message"],
                    "resolution_status": "consumed",
                },
                camera_available=self._camera_available(),
            )
            return True
        except Exception as error:
            logger.exception("cancel processing failed: input_id=%s", pending_input.input_id)
            self._store.finalize_pending_input_cycle(
                pending_input=pending_input,
                cycle_id=cycle_id,
                resolution_status="discarded",
                action_results=[],
                task_mutations=[],
                pending_input_mutations=[],
                discard_reason=CANCEL_FAILURE_REASON,
                ui_events=[],
                commit_payload={
                    "cycle_kind": "short",
                    "trigger_reason": "external_input",
                    "processed_input_id": pending_input.input_id,
                    "processed_input_kind": "cancel",
                    "emitted_event_types": [],
                    "executed_action_types": [],
                    "resolution_status": "discarded",
                    "error_kind": type(error).__name__,
                    "error_message": _error_message_text(error),
                },
                camera_available=self._camera_available(),
            )
            return False

    # Block: Settings change set iteration
    def _process_settings_change_set_once(self) -> bool:
        settings_change_set = self._store.claim_next_settings_change_set()
        if settings_change_set is None:
            return False
        logger.info(
            "claimed settings change set",
            extra={"change_set_id": settings_change_set.change_set_id},
        )
        try:
            self._store.finalize_settings_change_set(
                change_set=settings_change_set,
                default_settings=self._default_settings,
                final_status="applied",
                camera_available=self._camera_available(),
            )
            logger.info(
                "settings change set applied",
                extra={"change_set_id": settings_change_set.change_set_id},
            )
        except Exception as error:
            logger.exception(
                "settings editor processing failed: change_set_id=%s",
                settings_change_set.change_set_id,
            )
            self._store.finalize_settings_change_set(
                change_set=settings_change_set,
                default_settings=self._default_settings,
                final_status="rejected",
                reject_reason=f"{SETTINGS_CHANGE_SET_FAILURE_REASON}:{type(error).__name__}",
                camera_available=self._camera_available(),
            )
            logger.warning(
                "settings change set rejected",
                extra={
                    "change_set_id": settings_change_set.change_set_id,
                    "reject_reason": f"{SETTINGS_CHANGE_SET_FAILURE_REASON}:{type(error).__name__}",
                },
            )
        return True

    # Block: Settings override iteration
    def _process_settings_override_once(self) -> bool:
        settings_override = self._store.claim_next_settings_override()
        if settings_override is None:
            return False
        cycle_id = _opaque_id("cycle")
        logger.info(
            "claimed settings override",
            extra={
                "cycle_id": cycle_id,
                "override_id": settings_override.override_id,
                "setting_key": settings_override.key,
                "apply_scope": settings_override.apply_scope,
            },
        )
        try:
            self._store.append_input_journal_for_settings_override(
                settings_override=settings_override,
                cycle_id=cycle_id,
            )
            final_status, reject_reason = _evaluate_settings_override(settings_override)
            self._store.finalize_settings_override(
                override_id=settings_override.override_id,
                key=settings_override.key,
                requested_value_json=settings_override.requested_value_json,
                apply_scope=settings_override.apply_scope,
                cycle_id=cycle_id,
                final_status=final_status,
                reject_reason=reject_reason,
                camera_available=self._camera_available(),
            )
            logger.info(
                "settings override finalized",
                extra={
                    "cycle_id": cycle_id,
                    "override_id": settings_override.override_id,
                    "setting_key": settings_override.key,
                    "final_status": final_status,
                    "reject_reason": reject_reason,
                },
            )
        except Exception as error:
            logger.exception(
                "settings override processing failed: override_id=%s",
                settings_override.override_id,
            )
            self._store.finalize_settings_override(
                override_id=settings_override.override_id,
                key=settings_override.key,
                requested_value_json=settings_override.requested_value_json,
                apply_scope=settings_override.apply_scope,
                cycle_id=cycle_id,
                final_status="rejected",
                reject_reason=f"{SETTINGS_OVERRIDE_FAILURE_REASON}:{type(error).__name__}",
                camera_available=self._camera_available(),
            )
            logger.warning(
                "settings override rejected after failure",
                extra={
                    "cycle_id": cycle_id,
                    "override_id": settings_override.override_id,
                    "setting_key": settings_override.key,
                    "reject_reason": f"{SETTINGS_OVERRIDE_FAILURE_REASON}:{type(error).__name__}",
                },
            )
        return True

    # Block: Memory job iteration
    def _process_memory_job_once(self) -> bool:
        if not self._is_long_cycle_due():
            return False
        memory_job = self._store.claim_next_memory_job()
        if memory_job is None:
            return False
        logger.debug(
            "claimed memory job",
            extra={
                "job_id": memory_job.job_id,
                "job_kind": memory_job.job_kind,
                "tries": memory_job.tries,
            },
        )
        try:
            self._memory_job_handler(memory_job.job_kind)(memory_job)
            logger.debug(
                "memory job completed",
                extra={
                    "job_id": memory_job.job_id,
                    "job_kind": memory_job.job_kind,
                },
            )
        except Exception as error:
            logger.exception("memory job processing failed: job_id=%s", memory_job.job_id)
            self._store.fail_claimed_memory_job(
                memory_job=memory_job,
                error=error,
                max_tries=MAX_MEMORY_JOB_TRIES,
            )
        self._last_long_cycle_at_ms = _now_ms()
        return True

    # Block: Memory job dispatch
    def _memory_job_handler(self, job_kind: str) -> Callable[[MemoryJobRecord], None]:
        handlers = {
            "write_memory": self._run_write_memory_job,
            "refresh_preview": self._run_refresh_preview_job,
            "embedding_sync": self._run_embedding_sync_job,
            "quarantine_memory": self._run_quarantine_memory_job,
            "tidy_memory": self._run_tidy_memory_job,
        }
        handler = handlers.get(job_kind)
        if handler is None:
            raise RuntimeError(f"unsupported memory job kind: {job_kind}")
        return handler

    # Block: Memory job handlers
    def _run_write_memory_job(self, memory_job: MemoryJobRecord) -> None:
        self._store.complete_write_memory_job(memory_job=memory_job)

    def _run_refresh_preview_job(self, memory_job: MemoryJobRecord) -> None:
        effective_settings = self._store.read_effective_settings(self._default_settings)
        embedding_model = effective_settings["llm.embedding_model"]
        if not isinstance(embedding_model, str) or not embedding_model:
            raise RuntimeError("llm.embedding_model must be non-empty string")
        self._store.complete_refresh_preview_job(
            memory_job=memory_job,
            embedding_model=embedding_model,
        )

    def _run_embedding_sync_job(self, memory_job: MemoryJobRecord) -> None:
        self._store.complete_embedding_sync_job(memory_job=memory_job)

    def _run_quarantine_memory_job(self, memory_job: MemoryJobRecord) -> None:
        effective_settings = self._store.read_effective_settings(self._default_settings)
        embedding_model = effective_settings["llm.embedding_model"]
        if not isinstance(embedding_model, str) or not embedding_model:
            raise RuntimeError("llm.embedding_model must be non-empty string")
        self._store.complete_quarantine_memory_job(
            memory_job=memory_job,
            embedding_model=embedding_model,
        )

    def _run_tidy_memory_job(self, memory_job: MemoryJobRecord) -> None:
        self._store.complete_tidy_memory_job(memory_job=memory_job)

    # Block: Long cycle gate
    def _is_long_cycle_due(self) -> bool:
        now_ms = _now_ms()
        if self._last_long_cycle_at_ms == 0:
            return True
        min_interval_ms = self._long_cycle_min_interval_ms()
        return (now_ms - self._last_long_cycle_at_ms) >= min_interval_ms

    def _long_cycle_min_interval_ms(self) -> int:
        effective_settings = self._store.read_effective_settings(self._default_settings)
        min_interval_ms = effective_settings["runtime.long_cycle_min_interval_ms"]
        if isinstance(min_interval_ms, bool) or not isinstance(min_interval_ms, int):
            raise RuntimeError("runtime.long_cycle_min_interval_ms must be integer")
        if min_interval_ms <= 0:
            raise RuntimeError("runtime.long_cycle_min_interval_ms must be positive")
        return min_interval_ms

    # Block: Infinite loop
    def run_forever(self) -> None:
        logger.info("runtime loop started")
        try:
            while not self._stop_requested:
                processed = self.run_once()
                if not processed:
                    self._wait_for_next_runnable_cycle()
        finally:
            logger.info("runtime loop stopping")
            self._store.release_runtime_lease(owner_token=self._owner_token)

    # Block: Runtime stop request
    def request_stop(self) -> None:
        self._stop_requested = True

    # Block: Runtime activity tracking
    def _mark_runtime_activity(self) -> None:
        self._last_activity_at_ms = _now_ms()

    # Block: Idle timing
    def _idle_tick_ms(self) -> int:
        effective_settings = self._store.read_effective_settings(self._default_settings)
        idle_tick_ms = effective_settings["runtime.idle_tick_ms"]
        if isinstance(idle_tick_ms, bool) or not isinstance(idle_tick_ms, int):
            raise RuntimeError("runtime.idle_tick_ms must be integer")
        if idle_tick_ms <= 0:
            raise RuntimeError("runtime.idle_tick_ms must be positive")
        return idle_tick_ms

    # Block: Idle trigger state
    def _idle_duration_ms(self) -> int:
        return max(0, _now_ms() - self._last_activity_at_ms)

    def _idle_tick_due(self) -> bool:
        return self._idle_duration_ms() >= self._idle_tick_ms()

    # Block: Idle trigger claim
    def _claim_idle_tick_pending_input_if_due(self) -> PendingInputRecord | None:
        if not self._idle_tick_due():
            return None
        idle_duration_ms = self._idle_duration_ms()
        enqueue_result = self._store.enqueue_idle_tick(idle_duration_ms=idle_duration_ms)
        idle_input_id = str(enqueue_result["input_id"])
        logger.info(
            "idle tick enqueued",
            extra={
                "input_id": idle_input_id,
                "idle_duration_ms": idle_duration_ms,
            },
        )
        pending_input = self._store.claim_next_pending_input()
        if pending_input is None:
            raise RuntimeError("idle_tick must be claimable immediately after enqueue")
        if pending_input.input_id == idle_input_id:
            return pending_input
        self._store.discard_queued_pending_input(
            input_id=idle_input_id,
            discard_reason="idle_tick_superseded",
        )
        logger.info(
            "idle tick superseded by higher priority input",
            extra={
                "idle_input_id": idle_input_id,
                "claimed_input_id": pending_input.input_id,
                "claimed_input_kind": pending_input.payload["input_kind"],
            },
        )
        return pending_input

    # Block: Idle wait with queue polling
    def _wait_for_next_runnable_cycle(self) -> None:
        idle_due_at_ms = self._last_activity_at_ms + self._idle_tick_ms()
        while not self._stop_requested:
            work_state = self._store.read_runtime_work_state()
            if bool(work_state["has_short_cycle_work"]):
                return
            if bool(work_state["has_memory_job"]) and self._is_long_cycle_due():
                return
            now_ms = _now_ms()
            if now_ms >= idle_due_at_ms:
                return
            lease_refresh_due_at_ms = self._next_lease_refresh_due_at_ms()
            if now_ms >= lease_refresh_due_at_ms:
                self._refresh_runtime_lease()
                continue
            sleep_ms = min(
                idle_due_at_ms - now_ms,
                lease_refresh_due_at_ms - now_ms,
                DEFAULT_RUNTIME_WAIT_POLL_MS,
            )
            time.sleep(sleep_ms / 1000.0)

    # Block: Lease refresh
    def _refresh_runtime_lease(self) -> None:
        now_ms = _now_ms()
        if self._last_lease_refresh_at_ms != 0:
            elapsed_ms = now_ms - self._last_lease_refresh_at_ms
            if elapsed_ms < self._lease_heartbeat_ms:
                return
        self._store.acquire_runtime_lease(
            owner_token=self._owner_token,
            lease_ttl_ms=self._lease_ttl_ms,
        )
        self._last_lease_refresh_at_ms = now_ms

    # Block: Lease refresh deadline
    def _next_lease_refresh_due_at_ms(self) -> int:
        if self._last_lease_refresh_at_ms == 0:
            return 0
        return self._last_lease_refresh_at_ms + self._lease_heartbeat_ms

    # Block: Camera candidate helper
    def _camera_candidates_for_state(
        self,
        effective_settings: dict[str, Any],
    ) -> list[Any]:
        if not bool(effective_settings["sensors.camera.enabled"]):
            return []
        return self._camera_controller.list_candidates()

    # Block: Camera availability helper
    def _camera_available(self) -> bool:
        return self._camera_controller.is_available() and self._camera_sensor.is_available()


# Block: Runtime construction
def build_runtime_loop(*, db_path: Path | None = None) -> RuntimeLoop:
    resolved_db_path = db_path or _default_db_path()
    logger.info("initializing runtime loop", extra={"db_path": str(resolved_db_path)})
    store = SqliteStateStore(
        db_path=resolved_db_path,
        initializer_version=__version__,
    )
    store.initialize()
    default_settings = build_default_settings()
    return RuntimeLoop(
        store=store,
        owner_token=_runtime_owner_token(),
        default_settings=default_settings,
        cognition_client=_build_default_cognition_client(),
        search_client=_build_default_search_client(),
        camera_controller=_build_default_camera_controller(store=store),
        camera_sensor=_build_default_camera_sensor(store=store),
        speech_synthesizer=_build_default_speech_synthesizer(),
        lease_heartbeat_ms=_lease_heartbeat_ms(),
        lease_ttl_ms=_lease_ttl_ms(),
    )


# Block: Settings evaluation
def _evaluate_settings_override(settings_override: SettingsOverrideRecord) -> tuple[str, str | None]:
    try:
        definition = get_setting_definition(settings_override.key)
    except SettingsValidationError:
        return ("rejected", "unknown_settings_key")
    if settings_override.apply_scope not in definition.apply_scopes:
        return ("rejected", "invalid_settings_scope")
    try:
        decode_requested_value(settings_override.key, settings_override.requested_value_json)
    except SettingsValidationError:
        return ("rejected", "invalid_settings_value")
    return ("applied", None)


# Block: Error formatting
def _error_message_text(error: Exception) -> str:
    error_message = str(error).strip()
    if not error_message:
        return type(error).__name__
    return error_message[:240]


# Block: Pending input trigger reason
def _pending_input_trigger_reason(pending_input: PendingInputRecord) -> str:
    return normalize_trigger_reason(
        source=pending_input.source,
        payload=pending_input.payload,
    )


# Block: Pending input observation hint
def _pending_input_observation_hint(pending_input: PendingInputRecord) -> str:
    input_kind = str(pending_input.payload["input_kind"])
    if input_kind == "chat_message":
        text = pending_input.payload.get("text")
        attachments = pending_input.payload.get("attachments")
        normalized_text = text.strip() if isinstance(text, str) else ""
        if attachments is not None and not isinstance(attachments, list):
            raise RuntimeError("chat_message.attachments must be a list")
        attachment_count = len(attachments) if isinstance(attachments, list) else 0
        if normalized_text and attachment_count > 0:
            return f"{normalized_text} カメラ画像 {attachment_count} 枚"
        if normalized_text:
            return normalized_text
        if attachment_count > 0:
            return f"カメラ画像 {attachment_count} 枚"
        raise RuntimeError("chat_message requires text or attachments")
    if input_kind == "microphone_message":
        text = pending_input.payload.get("text")
        if not isinstance(text, str) or not text.strip():
            raise RuntimeError("microphone_message.text must be non-empty string")
        return f"音声入力: {text.strip()}"
    if input_kind == "camera_observation":
        attachments = pending_input.payload.get("attachments")
        if not isinstance(attachments, list):
            raise RuntimeError("camera_observation.attachments must be a list")
        attachment_count = len(attachments)
        if attachment_count <= 0:
            raise RuntimeError("camera_observation.attachments must not be empty")
        camera_names = [
            str(attachment["camera_display_name"])
            for attachment in attachments
            if isinstance(attachment, dict)
            and isinstance(attachment.get("camera_display_name"), str)
            and attachment["camera_display_name"]
        ]
        camera_label = ""
        if camera_names:
            camera_label = f" ({' / '.join(dict.fromkeys(camera_names[:3]))})"
        trigger_reason = pending_input.payload.get("trigger_reason")
        if trigger_reason == "post_action_followup":
            return f"カメラ画像 {attachment_count} 枚{camera_label}を追跡観測"
        return f"カメラ画像 {attachment_count} 枚{camera_label}を自発観測"
    if input_kind == "network_result":
        summary_text = pending_input.payload.get("summary_text")
        query = pending_input.payload.get("query")
        if not isinstance(summary_text, str) or not summary_text.strip():
            raise RuntimeError("network_result.summary_text must be non-empty string")
        if not isinstance(query, str) or not query.strip():
            raise RuntimeError("network_result.query must be non-empty string")
        return f"{query.strip()} {summary_text.strip()}"
    if input_kind == "idle_tick":
        idle_duration_ms = pending_input.payload.get("idle_duration_ms")
        if isinstance(idle_duration_ms, bool) or not isinstance(idle_duration_ms, int):
            raise RuntimeError("idle_tick.idle_duration_ms must be integer")
        if idle_duration_ms <= 0:
            raise RuntimeError("idle_tick.idle_duration_ms must be positive")
        return f"{idle_duration_ms}ms の idle_tick が到来した"
    raise RuntimeError("unsupported input_kind for cognition observation hint")


# Block: Unsupported input handling
def _unsupported_input_events(
    pending_input: PendingInputRecord,
    resolved_at: int,
) -> tuple[list[dict[str, Any]], list[ActionHistoryRecord]]:
    input_kind = str(pending_input.payload["input_kind"])
    ui_events = [
        {
            "channel": pending_input.channel,
            "event_type": "error",
            "payload": {
                "error_code": "unsupported_input_kind",
                "message": "未対応の入力種別です",
                "retriable": False,
            },
        }
    ]
    action_results = [
        ActionHistoryRecord(
            result_id=_opaque_id("actres"),
            command_id=_opaque_id("cmd"),
            action_type="emit_input_error",
            command={
                "target_channel": pending_input.channel,
                "input_kind": input_kind,
                "event_types": ["error"],
            },
            started_at=resolved_at,
            finished_at=resolved_at + 1,
            status="succeeded",
            failure_mode=None,
            observed_effects={
                "emitted_event_types": ["error"],
                "error_code": "unsupported_input_kind",
            },
            raw_result_ref=None,
            adapter_trace_ref=None,
        )
    ]
    return (ui_events, action_results)


# Block: Failed input handling
def _failed_pending_input_events(
    pending_input: PendingInputRecord,
    error: Exception,
) -> list[dict[str, Any]]:
    return [
        {
            "channel": pending_input.channel,
            "event_type": "error",
            "payload": {
                "error_code": PENDING_INPUT_FAILURE_REASON,
                "message": f"入力処理に失敗しました: {_error_message_text(error)}",
                "retriable": False,
            },
        }
    ]

# Block: Runtime helpers
def _default_db_path() -> Path:
    return Path(__file__).resolve().parents[3] / "data" / "core.sqlite3"


def _runtime_owner_token() -> str:
    return f"runtime_{uuid.uuid4().hex}"


# Block: Lease timing helpers
def _lease_heartbeat_ms() -> int:
    return DEFAULT_LEASE_HEARTBEAT_MS


def _lease_ttl_ms() -> int:
    return DEFAULT_LEASE_TTL_MS


# Block: Cognition client factory
def _build_default_cognition_client() -> CognitionClient:
    from otomekairo.infra.litellm_cognition_client import LiteLLMCognitionClient

    return LiteLLMCognitionClient()


def _build_default_search_client() -> SearchClient:
    from otomekairo.infra.duckduckgo_search_client import DuckDuckGoSearchClient

    return DuckDuckGoSearchClient()


# Block: Speech synthesizer factory
def _build_default_speech_synthesizer() -> SpeechSynthesizer:
    from otomekairo.infra.aivis_cloud_speech_synthesizer import (
        AivisCloudSpeechSynthesizer,
    )
    from otomekairo.infra.speech_synthesis_common import default_tts_audio_dir
    from otomekairo.infra.style_bert_vits2_speech_synthesizer import (
        StyleBertVits2SpeechSynthesizer,
    )
    from otomekairo.infra.switching_speech_synthesizer import (
        SwitchingSpeechSynthesizer,
    )
    from otomekairo.infra.voicevox_speech_synthesizer import (
        VoicevoxSpeechSynthesizer,
    )

    audio_output_dir = default_tts_audio_dir()
    return SwitchingSpeechSynthesizer(
        provider_synthesizers={
            "aivis-cloud": AivisCloudSpeechSynthesizer(
                audio_output_dir=audio_output_dir,
            ),
            "voicevox": VoicevoxSpeechSynthesizer(
                audio_output_dir=audio_output_dir,
            ),
            "style-bert-vits2": StyleBertVits2SpeechSynthesizer(
                audio_output_dir=audio_output_dir,
            ),
        }
    )


# Block: Camera controller factory
def _build_default_camera_controller(*, store: SqliteStateStore) -> CameraController:
    from otomekairo.infra.wifi_camera_controller import WiFiCameraController

    return WiFiCameraController(
        camera_connections_loader=store.read_enabled_camera_connections,
    )


# Block: Camera sensor factory
def _build_default_camera_sensor(*, store: SqliteStateStore) -> CameraSensor:
    from otomekairo.infra.wifi_camera_sensor import WiFiCameraSensor

    return WiFiCameraSensor(
        camera_connections_loader=store.read_enabled_camera_connections,
    )


# Block: Runtime helper ids
def _opaque_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _now_ms() -> int:
    return int(time.time() * 1000)


# Block: Failed task helpers
def _failed_task_events(*, task: TaskStateRecord, cycle_id: str) -> list[dict[str, Any]]:
    target_channel = task.completion_hint.get("target_channel")
    if not isinstance(target_channel, str) or not target_channel:
        raise RuntimeError("browse task completion_hint.target_channel must be non-empty string")
    return [
        {
            "channel": target_channel,
            "event_type": "error",
            "payload": {
                "error_code": "browse_task_failed",
                "message": "外部検索タスクに失敗しました",
                "retriable": False,
                "cycle_id": cycle_id,
                "task_id": task.task_id,
            },
        }
    ]


def _failed_task_action_result(
    *,
    task: TaskStateRecord,
    cycle_id: str,
    error: Exception,
) -> ActionHistoryRecord:
    target_channel = task.completion_hint.get("target_channel")
    if not isinstance(target_channel, str) or not target_channel:
        raise RuntimeError("browse task completion_hint.target_channel must be non-empty string")
    started_at = _now_ms()
    finished_at = started_at + 1
    return ActionHistoryRecord(
        result_id=_opaque_id("actres"),
        command_id=_opaque_id("cmd"),
        action_type="abandon_browse_task",
        command={
            "target_channel": target_channel,
            "target": {
                "queue": "task_state",
                "channel": target_channel,
            },
            "event_types": ["error"],
            "decision": "execute",
            "decision_reason": "task_resume_failed",
            "related_task_id": task.task_id,
            "command_type": "abandon_browse_task",
            "parameters": {
                "query": task.goal_hint,
            },
        },
        started_at=started_at,
        finished_at=finished_at,
        status="failed",
        failure_mode=type(error).__name__,
        observed_effects={
            "emitted_event_types": ["error"],
            "related_task_id": task.task_id,
            "task_status_after": "abandoned",
            "error_message": _error_message_text(error),
        },
        raw_result_ref=None,
        adapter_trace_ref={
            "cycle_id": cycle_id,
            "error_kind": type(error).__name__,
            "error_message": _error_message_text(error),
        },
    )
