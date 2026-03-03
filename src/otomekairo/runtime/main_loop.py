"""Minimal runtime loop for consuming pending inputs."""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any, Callable

from otomekairo import __version__
from otomekairo.gateway.cognition_client import CognitionClient
from otomekairo.gateway.notification_client import NotificationClient
from otomekairo.gateway.search_client import SearchClient
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
from otomekairo.usecase.run_browse_task import run_browse_task
from otomekairo.usecase.run_cognition import run_cognition_for_browser_chat_input


# Block: Runtime constants
DEFAULT_LEASE_HEARTBEAT_MS = 5_000
MINIMUM_LEASE_TTL_MS = 15_000
DEFAULT_LEASE_TTL_MS = 60_000
MAX_MEMORY_JOB_TRIES = 3
PENDING_INPUT_FAILURE_REASON = "processing_failed"
SETTINGS_OVERRIDE_FAILURE_REASON = "settings_processing_failed"
SETTINGS_CHANGE_SET_FAILURE_REASON = "settings_editor_processing_failed"
CANCEL_FAILURE_REASON = "cancel_processing_failed"


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
        notification_client: NotificationClient,
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
        self._notification_client = notification_client
        self._lease_heartbeat_ms = lease_heartbeat_ms
        self._lease_ttl_ms = lease_ttl_ms
        self._boot_reconciled = False
        self._prefer_long_cycle = False
        self._last_long_cycle_at_ms = 0

    # Block: Single iteration
    def run_once(self) -> bool:
        self._refresh_runtime_lease()
        if not self._boot_reconciled:
            self._store.materialize_next_boot_settings()
            self._boot_reconciled = True
        if self._prefer_long_cycle:
            processed_memory = self._process_memory_job_once()
            if processed_memory:
                self._prefer_long_cycle = False
                return True
        processed_editor_settings = self._process_settings_change_set_once()
        if processed_editor_settings:
            self._prefer_long_cycle = True
            return True
        processed_settings = self._process_settings_override_once()
        if processed_settings:
            self._prefer_long_cycle = True
            return True
        pending_input = self._store.claim_next_pending_input()
        if pending_input is not None:
            self._process_claimed_pending_input(pending_input)
            self._prefer_long_cycle = True
            return True
        waiting_task = self._store.claim_next_waiting_browse_task()
        if waiting_task is not None:
            self._process_claimed_waiting_task(waiting_task)
            self._prefer_long_cycle = True
            return True
        processed_memory = self._process_memory_job_once()
        if processed_memory:
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
                resolution_status,
                discard_reason,
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
            }
            self._store.finalize_pending_input_cycle(
                pending_input=pending_input,
                cycle_id=cycle_id,
                resolution_status=resolution_status,
                action_results=action_results,
                task_mutations=task_mutations,
                discard_reason=discard_reason,
                ui_events=ui_events,
                commit_payload=commit_payload,
            )
        except Exception as error:
            ui_events = _failed_pending_input_events(pending_input)
            self._store.finalize_pending_input_cycle(
                pending_input=pending_input,
                cycle_id=cycle_id,
                resolution_status="discarded",
                action_results=[],
                task_mutations=[],
                discard_reason=PENDING_INPUT_FAILURE_REASON,
                ui_events=ui_events,
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
            )
        except Exception as error:
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
        str,
        str | None,
    ]:
        input_kind = pending_input.payload["input_kind"]
        if input_kind in {"chat_message", "network_result"}:
            state_snapshot = self._store.read_cognition_state(
                self._default_settings,
                observation_hint_text=_pending_input_observation_hint(pending_input),
            )
            cognition_input = build_cognition_input(
                pending_input=pending_input,
                cycle_id=cycle_id,
                resolved_at=resolved_at,
                state_snapshot=state_snapshot,
            )
            cognition_execution = run_cognition_for_browser_chat_input(
                pending_input=pending_input,
                cycle_id=cycle_id,
                resolved_at=resolved_at,
                cognition_input=cognition_input,
                effective_settings=state_snapshot.effective_settings,
                cognition_client=self._cognition_client,
                notification_client=self._notification_client,
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
                "consumed",
                None,
            )
        if input_kind == "cancel":
            return ([], [], [], "discarded", "cancel_target_not_found")
        ui_events, action_results = _unsupported_input_events(pending_input, resolved_at)
        self._append_ui_events(cycle_id=cycle_id, ui_events=ui_events)
        return (
            ui_events,
            action_results,
            [],
            "discarded",
            "unsupported_input_kind",
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
            )
            return True
        except Exception as error:
            self._store.finalize_pending_input_cycle(
                pending_input=pending_input,
                cycle_id=cycle_id,
                resolution_status="discarded",
                action_results=[],
                task_mutations=[],
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
            )
            return False

    # Block: Settings change set iteration
    def _process_settings_change_set_once(self) -> bool:
        settings_change_set = self._store.claim_next_settings_change_set()
        if settings_change_set is None:
            return False
        try:
            self._store.finalize_settings_change_set(
                change_set=settings_change_set,
                default_settings=self._default_settings,
                final_status="applied",
            )
        except Exception as error:
            self._store.finalize_settings_change_set(
                change_set=settings_change_set,
                default_settings=self._default_settings,
                final_status="rejected",
                reject_reason=f"{SETTINGS_CHANGE_SET_FAILURE_REASON}:{type(error).__name__}",
            )
        return True

    # Block: Settings override iteration
    def _process_settings_override_once(self) -> bool:
        settings_override = self._store.claim_next_settings_override()
        if settings_override is None:
            return False
        cycle_id = _opaque_id("cycle")
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
            )
        except Exception as error:
            self._store.finalize_settings_override(
                override_id=settings_override.override_id,
                key=settings_override.key,
                requested_value_json=settings_override.requested_value_json,
                apply_scope=settings_override.apply_scope,
                cycle_id=cycle_id,
                final_status="rejected",
                reject_reason=f"{SETTINGS_OVERRIDE_FAILURE_REASON}:{type(error).__name__}",
            )
        return True

    # Block: Memory job iteration
    def _process_memory_job_once(self) -> bool:
        if not self._is_long_cycle_due():
            return False
        memory_job = self._store.claim_next_memory_job()
        if memory_job is None:
            return False
        try:
            self._memory_job_handler(memory_job.job_kind)(memory_job)
        except Exception as error:
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
        try:
            while True:
                processed = self.run_once()
                if not processed:
                    self._sleep_until_next_idle_tick()
        finally:
            self._store.release_runtime_lease(owner_token=self._owner_token)

    # Block: Idle timing
    def _idle_tick_ms(self) -> int:
        effective_settings = self._store.read_effective_settings(self._default_settings)
        idle_tick_ms = effective_settings["runtime.idle_tick_ms"]
        if isinstance(idle_tick_ms, bool) or not isinstance(idle_tick_ms, int):
            raise RuntimeError("runtime.idle_tick_ms must be integer")
        if idle_tick_ms <= 0:
            raise RuntimeError("runtime.idle_tick_ms must be positive")
        return idle_tick_ms

    # Block: Idle wait with heartbeat
    def _sleep_until_next_idle_tick(self) -> None:
        remaining_ms = self._idle_tick_ms()
        while remaining_ms > 0:
            sleep_ms = min(remaining_ms, self._lease_heartbeat_ms)
            time.sleep(sleep_ms / 1000.0)
            remaining_ms -= sleep_ms
            if remaining_ms > 0:
                self._refresh_runtime_lease()

    # Block: Lease refresh
    def _refresh_runtime_lease(self) -> None:
        self._store.acquire_runtime_lease(
            owner_token=self._owner_token,
            lease_ttl_ms=self._lease_ttl_ms,
        )


# Block: Runtime construction
def build_runtime_loop(*, db_path: Path | None = None) -> RuntimeLoop:
    resolved_db_path = db_path or _default_db_path()
    store = SqliteStateStore(
        db_path=resolved_db_path,
        initializer_version=__version__,
    )
    store.initialize()
    return RuntimeLoop(
        store=store,
        owner_token=_runtime_owner_token(),
        default_settings=build_default_settings(),
        cognition_client=_build_default_cognition_client(),
        search_client=_build_default_search_client(),
        notification_client=_build_default_notification_client(),
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
    input_kind = str(pending_input.payload["input_kind"])
    if input_kind == "network_result":
        return "external_result"
    return "external_input"


# Block: Pending input observation hint
def _pending_input_observation_hint(pending_input: PendingInputRecord) -> str:
    input_kind = str(pending_input.payload["input_kind"])
    if input_kind == "chat_message":
        text = pending_input.payload.get("text")
        if not isinstance(text, str) or not text.strip():
            raise RuntimeError("chat_message.text must be non-empty string")
        return text.strip()
    if input_kind == "network_result":
        summary_text = pending_input.payload.get("summary_text")
        query = pending_input.payload.get("query")
        if not isinstance(summary_text, str) or not summary_text.strip():
            raise RuntimeError("network_result.summary_text must be non-empty string")
        if not isinstance(query, str) or not query.strip():
            raise RuntimeError("network_result.query must be non-empty string")
        return f"{query.strip()} {summary_text.strip()}"
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
def _failed_pending_input_events(pending_input: PendingInputRecord) -> list[dict[str, Any]]:
    return [
        {
            "channel": pending_input.channel,
            "event_type": "error",
            "payload": {
                "error_code": PENDING_INPUT_FAILURE_REASON,
                "message": "入力処理に失敗しました",
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


# Block: Notification client factory
def _build_default_notification_client() -> NotificationClient:
    from otomekairo.infra.line_notification_client import LineNotificationClient

    return LineNotificationClient()


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
